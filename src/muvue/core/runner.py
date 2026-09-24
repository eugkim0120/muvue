"""Unattended runner (plan section 6 "Runner (unattended)", P5).

`run(...)` is the single entry point the CLI (`muvue run`) is a thin
wrapper over. It spawns one driver subprocess per ready node (fresh
context each time -- no shared process state between nodes, per plan
section 1 principle 1), records real usage to `node_usage`, and applies
routing / budget / rate-limit policy from `config.toml`.

Holds no in-memory daemon state across cycles the way `core.daemon`
doesn't either: every unit of work (scheduling read, or one node's
run) opens its own short-lived `sqlite3.Connection` via `db_path`, so a
killed-and-restarted `run` invocation loses nothing but in-flight
subprocess output (the node's lease already reverts via
`core.daemon.reconcile_leases`, called at the top of every `run`, exactly
like `muvue serve`'s reconcile-on-start -- plan section 5's "Daemon
reconciles on start", extended here to the runner)."""

from __future__ import annotations

import concurrent.futures
import fnmatch
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import daemon as daemon_mod
from . import db as core_db
from . import drivers
from . import events as events_mod
from . import nodes as nodes_mod
from . import queries
from . import spend as spend_mod
from .config import MuvueConfig

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# Node states the runner treats as "needs a human, stop scheduling new
# work until it's resolved" (P5 acceptance criterion 2). Deliberately
# global, not per-node-skip: the simplest reading of "pauses ... when it
# hits a node in [this] state" that stays deterministic and testable
# without inventing an unstated priority/skip policy (see
# docs/decisions.md).
_GATING_STATUSES = ("awaiting_approval", "blocked", "failed")

# Outcomes from a just-processed node that also mean "stop scheduling new
# work and return control" this cycle.
_BLOCKING_OUTCOMES = {"blocked_rate_limit", "blocked_unavailable", "failed", "review"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Budget (plan section 6 "Runner (unattended)": stop at 100%, warn at 80%)
# --------------------------------------------------------------------------


def budget_state(conn, config: MuvueConfig) -> dict:
    unit = config.budget.unit
    if unit == "usd":
        column_expr = "COALESCE(SUM(cost), 0)"
    elif unit == "tokens":
        column_expr = "COALESCE(SUM(in_tokens + out_tokens), 0)"
    else:  # "requests" (config.budget.unit's third option; see docs/decisions.md
        # for how this lines up with AgentConfig.cost_model's "quota")
        column_expr = "COALESCE(SUM(requests), 0)"
    spent = conn.execute(f"SELECT {column_expr} c FROM node_usage").fetchone()["c"]
    limit = config.budget.limit
    pct = (spent / limit) if limit else 0.0
    return {
        "unit": unit,
        "spent": spent,
        "limit": limit,
        "pct": pct,
        "exhausted": bool(limit) and spent >= limit,
        "warn": bool(limit) and pct >= 0.8,
    }


# --------------------------------------------------------------------------
# Rate-limit reconcile (extends core.daemon's reconcile-on-start pattern
# to blocked(rate_limit) nodes whose retry_at has passed -- plan section 5)
# --------------------------------------------------------------------------


def reconcile_rate_limits(conn, *, now: datetime | None = None) -> list[int]:
    current = (now or _now()).strftime(TS_FORMAT)
    rows = conn.execute(
        "SELECT id FROM nodes WHERE status = 'blocked' AND block_reason = 'rate_limit' "
        "AND deleted_at IS NULL"
    ).fetchall()
    unblocked = []
    for row in rows:
        node_id = row["id"]
        latest = conn.execute(
            "SELECT payload FROM events WHERE node_id = ? AND type = 'runner.rate_limited' "
            "ORDER BY id DESC LIMIT 1",
            (node_id,),
        ).fetchone()
        if latest is None:
            continue
        retry_at = json.loads(latest["payload"]).get("retry_at")
        if retry_at is not None and retry_at <= current:
            nodes_mod.ready(conn, node_id, actor="daemon", actor_evidence="subprocess")
            unblocked.append(node_id)
    return unblocked


# --------------------------------------------------------------------------
# Routing (plan section 6 last bullet: `[routing] kind -> agent`)
# --------------------------------------------------------------------------


def agent_for_node(node, config: MuvueConfig, override: str | None) -> str:
    if override:
        return override
    return getattr(config.routing, node["kind"])


# --------------------------------------------------------------------------
# Scheduler: disjoint predicted_touches, capped by --parallel and each
# agent's own max_concurrency (plan section 6 "--parallel N").
# --------------------------------------------------------------------------


def _ready_nodes(conn, project_id: int | None):
    """`task`/`subtask` nodes only -- a `ready` `spec` node means "ready
    for decomposition" (Gate 1, `core.gates.submit_spec`/`approve_spec`),
    a distinct agent workflow (`spec`/`decompose` CLI verbs) this runner
    doesn't drive: nothing in plan section 6 describes the unattended
    runner performing decomposition, and `[routing]` having a `spec` entry
    at all is there for a future decomposition-driving use, not this
    one -- see docs/decisions.md."""
    if project_id is not None:
        return conn.execute(
            "SELECT * FROM nodes WHERE status = 'ready' AND deleted_at IS NULL "
            "AND kind IN ('task', 'subtask') AND project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM nodes WHERE status = 'ready' AND deleted_at IS NULL "
        "AND kind IN ('task', 'subtask') ORDER BY id"
    ).fetchall()


def _touches(conn, node_id: int) -> list[str]:
    return [
        r["path_glob"]
        for r in conn.execute(
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (node_id,)
        ).fetchall()
    ]


def _touch_sets_overlap(a: list[str], b: list[str]) -> bool:
    """Two glob sets "overlap" if either could match a path the other
    names literally, or the globs are identical -- a conservative,
    symmetric check since neither side knows the other's real file list
    yet (P0/P1's `predicted_touches` is a prediction, not a diff)."""
    if not a or not b:
        return False
    return any(
        x == y or fnmatch.fnmatch(x, y) or fnmatch.fnmatch(y, x) for x in a for y in b
    )


def select_batch(
    conn, ready_rows, config: MuvueConfig, *, parallel: int, agent_override: str | None
) -> list[tuple]:
    """Greedily select up to `parallel` ready nodes whose predicted_touches
    are pairwise disjoint, never exceeding any one agent's
    `max_concurrency` within the batch. Skips (does not select) a node
    whose routed agent isn't configured at all, rather than crashing --
    it simply stays `ready` for a human to notice via `muvue status`."""
    selected: list[tuple] = []
    selected_touches: list[list[str]] = []
    agent_counts: dict[str, int] = {}
    for node in ready_rows:
        if len(selected) >= parallel:
            break
        agent_name = agent_for_node(node, config, agent_override)
        agent_cfg = config.agents.get(agent_name)
        if agent_cfg is None:
            continue
        if agent_counts.get(agent_name, 0) >= agent_cfg.max_concurrency:
            continue
        touches = _touches(conn, node["id"])
        if any(_touch_sets_overlap(touches, t) for t in selected_touches):
            continue
        selected.append((node, agent_name, agent_cfg))
        selected_touches.append(touches)
        agent_counts[agent_name] = agent_counts.get(agent_name, 0) + 1
    return selected


# --------------------------------------------------------------------------
# Per-node execution
# --------------------------------------------------------------------------


# v4 section 2: `[agents.<x>.budget].unit` must be "expressible by this
# driver's cost_model" -- `cost_model` -> the `agent_spend` unit/amount
# that model actually produces. Budget *enforcement* against a configured
# limit is out of scope this phase (see core.spend's module docstring);
# this just decides what to accumulate.
_COST_MODEL_TO_SPEND = {
    "usd": lambda r: ("usd", r.cost),
    "tokens": lambda r: ("tokens", r.in_tokens + r.out_tokens),
    "quota": lambda r: ("requests", r.requests),
}


def _record_usage(
    conn, node_id: int, agent_name: str, result: drivers.DriverResult, *, cost_model: str | None = None
) -> None:
    with core_db.write_txn(conn):
        conn.execute(
            "INSERT INTO node_usage (node_id, agent, model, in_tokens, out_tokens, requests, "
            "cost, rate_limited) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node_id, agent_name, result.model, result.in_tokens, result.out_tokens,
                result.requests, result.cost, int(result.status == "rate_limited"),
            ),
        )
        if cost_model in _COST_MODEL_TO_SPEND:
            unit, amount = _COST_MODEL_TO_SPEND[cost_model](result)
            project_id = nodes_mod.get_node(conn, node_id)["project_id"]
            spend_mod.record_spend(
                conn, project_id, agent_name, unit, amount, actor="daemon",
                actor_evidence="subprocess",
            )


def _apply_rate_limit(conn, node, owner: str, agent_name: str, result: drivers.DriverResult) -> str:
    retry_seconds = result.retry_after_seconds or 60
    retry_at = (_now() + timedelta(seconds=retry_seconds)).strftime(TS_FORMAT)
    with core_db.write_txn(conn):
        nodes_mod.block(
            conn, node["id"], reason="rate_limit", actor=owner, event_actor_role="daemon",
            actor_evidence="subprocess",
        )
        events_mod.record_event(
            conn, project_id=node["project_id"], node_id=node["id"], actor="daemon",
            actor_evidence="subprocess",
            type_="runner.rate_limited",
            payload={"agent": agent_name, "retry_at": retry_at, "retry_after_seconds": retry_seconds},
        )
    return retry_at


def run_node(
    conn,
    node,
    agent_name: str,
    agent_cfg,
    config: MuvueConfig,
    repo_root,
    *,
    invoke: Callable = drivers.invoke_driver,
) -> dict:
    """Run one node to a terminal outcome for this cycle: `start` (binds a
    strict-mode worktree if configured), spawn the driver with `brief` on
    stdin, then `done`/`fail`/`block` based on what it reports. Returns a
    small outcome dict, never raises for a driver-side failure (only a
    genuine `core` programming error propagates)."""
    owner = f"runner:{agent_name}"
    started = nodes_mod.start(
        conn, node["id"], owner=owner, config=config, repo_root=repo_root,
        lease_minutes=config.planning.lease_minutes, actor_evidence="subprocess",
    )
    if started["noop"]:
        return {"node_id": node["id"], "agent": agent_name, "outcome": "noop"}
    node_row = started["node"]
    brief = queries.brief_node(conn, node["id"])
    brief_text = json.dumps(brief, default=str)
    cwd = node_row.get("worktree") or str(repo_root)

    result = invoke(agent_name, agent_cfg, brief_text, Path(cwd))

    if result.status == "unavailable":
        return _handle_unavailable(conn, node_row, owner, agent_name, agent_cfg, config, repo_root, result, invoke)

    _record_usage(conn, node["id"], agent_name, result, cost_model=agent_cfg.cost_model)

    if result.status == "rate_limited":
        retry_at = _apply_rate_limit(conn, node_row, owner, agent_name, result)
        return _apply_on_rate_limit(conn, node_row, agent_name, agent_cfg, config, repo_root, retry_at, invoke)

    if result.status == "done":
        outcome = nodes_mod.done(
            conn, node["id"], owner=owner, summary=result.summary or None, config=config,
            run_checks=None, cwd=cwd, actor_evidence="subprocess",
        )
        node_status = outcome["node"]["status"]
        return {
            "node_id": node["id"], "agent": agent_name,
            "outcome": node_status,  # "done" (auto-approved) or "review" (flagged)
            "auto_approved": outcome.get("auto_approved", True),
        }

    # "failed": the driver ran and cleanly reported it could not finish.
    nodes_mod.fail(
        conn, node["id"], owner=owner, lesson=result.error or "driver reported failure",
        trigger="driver_failed", actor_evidence="subprocess",
    )
    after = nodes_mod.get_node(conn, node["id"])
    return {
        "node_id": node["id"], "agent": agent_name,
        "outcome": "failed" if after["status"] == "failed" else "retry_ready",
    }


def _apply_on_rate_limit(conn, node, agent_name, agent_cfg, config, repo_root, retry_at, invoke) -> dict:
    """Plan section 6: "runner applies `config.agents.<x>.on_rate_limit`
    (`wait`/`fallback:<agent>`/`pause`)"."""
    mode = agent_cfg.on_rate_limit
    if mode.startswith("fallback:"):
        fallback_name = mode.split(":", 1)[1]
        fallback_cfg = config.agents.get(fallback_name)
        if fallback_cfg is None:
            return {
                "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
                "retry_at": retry_at,
                "error": f"on_rate_limit fallback agent {fallback_name!r} is not configured",
            }
        nodes_mod.ready(conn, node["id"], actor="daemon", actor_evidence="subprocess")
        fresh = nodes_mod.get_node(conn, node["id"])
        result = run_node(conn, fresh, fallback_name, fallback_cfg, config, repo_root, invoke=invoke)
        result["fallback_from"] = agent_name
        return result
    return {
        "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
        "retry_at": retry_at, "on_rate_limit": mode,
    }


def _handle_unavailable(conn, node_row, owner, agent_name, agent_cfg, config, repo_root, result, invoke) -> dict:
    """A failing `auth_check` (not logged in, CLI not installed, etc.) is a
    driver-unavailable condition, not a crash (P5 prompt requirement).
    Reuses `on_rate_limit`'s `fallback:<agent>` config -- the plan defines
    no separate `on_unavailable` key, and "another agent can pick this up"
    is the same policy either way (see docs/decisions.md)."""
    mode = agent_cfg.on_rate_limit
    if mode.startswith("fallback:"):
        fallback_name = mode.split(":", 1)[1]
        fallback_cfg = config.agents.get(fallback_name)
        if fallback_cfg is not None:
            nodes_mod.ready(conn, node_row["id"], actor="daemon", actor_evidence="subprocess")
            fresh = nodes_mod.get_node(conn, node_row["id"])
            res = run_node(conn, fresh, fallback_name, fallback_cfg, config, repo_root, invoke=invoke)
            res["fallback_from"] = agent_name
            res["fallback_reason"] = "driver_unavailable"
            return res
    with core_db.write_txn(conn):
        nodes_mod.block(
            conn, node_row["id"], reason="external", actor=owner, event_actor_role="daemon",
            actor_evidence="subprocess",
        )
        events_mod.record_event(
            conn, project_id=node_row["project_id"], node_id=node_row["id"], actor="daemon",
            actor_evidence="subprocess",
            type_="runner.driver_unavailable", payload={"agent": agent_name, "error": result.error},
        )
    return {
        "node_id": node_row["id"], "agent": agent_name, "outcome": "blocked_unavailable",
        "error": result.error,
    }


# --------------------------------------------------------------------------
# Top-level entry point
# --------------------------------------------------------------------------


def _gating_nodes(conn, project_id: int | None) -> list[dict]:
    if project_id is not None:
        rows = conn.execute(
            "SELECT id, status, block_reason FROM nodes WHERE deleted_at IS NULL "
            "AND project_id = ? AND status IN ('awaiting_approval', 'blocked', 'failed')",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, status, block_reason FROM nodes WHERE deleted_at IS NULL "
            "AND status IN ('awaiting_approval', 'blocked', 'failed')"
        ).fetchall()
    return [dict(r) for r in rows]


def run(
    db_path,
    config: MuvueConfig,
    repo_root,
    *,
    agent_override: str | None = None,
    parallel: int = 1,
    project_id: int | None = None,
    invoke: Callable = drivers.invoke_driver,
    max_cycles: int = 1000,
) -> dict:
    """Run every schedulable ready node to a terminal outcome, unattended,
    until there's nothing left to schedule, the budget is exhausted, or a
    node needs human attention (P5 acceptance criteria 1, 2, 6)."""
    repo_root = Path(repo_root)
    parallel = max(1, parallel)

    conn = core_db.connect(db_path)
    try:
        daemon_mod.reconcile_leases(conn)
        reconcile_rate_limits(conn)
    finally:
        conn.close()

    processed: list[dict] = []
    warned = False
    paused = None
    cycles = 0

    while cycles < max_cycles:
        cycles += 1
        conn = core_db.connect(db_path)
        try:
            gating = _gating_nodes(conn, project_id)
            if gating:
                paused = {"reason": "nodes_need_attention", "nodes": gating}
                break

            state = budget_state(conn, config)
            if state["exhausted"]:
                paused = {"reason": "budget_exhausted", "budget": state}
                break
            if state["warn"] and not warned:
                with core_db.write_txn(conn):
                    events_mod.record_event(
                        conn, project_id=None, node_id=None, actor="daemon",
                        actor_evidence="subprocess",
                        type_="runner.budget_warning", payload=state,
                    )
                warned = True

            ready_rows = _ready_nodes(conn, project_id)
            batch = select_batch(conn, ready_rows, config, parallel=parallel, agent_override=agent_override)
        finally:
            conn.close()

        if not batch:
            break

        def _exec(item):
            node, agent_name, agent_cfg = item
            c = core_db.connect(db_path)
            try:
                return run_node(c, node, agent_name, agent_cfg, config, repo_root, invoke=invoke)
            finally:
                c.close()

        if parallel == 1:
            results = [_exec(item) for item in batch]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as ex:
                results = list(ex.map(_exec, batch))

        processed.extend(results)
        blocking = [r for r in results if r.get("outcome") in _BLOCKING_OUTCOMES]
        if blocking:
            paused = {
                "reason": "node_blocked",
                "outcomes": sorted({r["outcome"] for r in blocking}),
            }
            break

    conn = core_db.connect(db_path)
    try:
        final_budget = budget_state(conn, config)
    finally:
        conn.close()

    return {"processed": processed, "paused": paused, "cycles": cycles, "budget": final_budget}
