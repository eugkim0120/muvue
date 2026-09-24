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
# Budget (v4 section 2/6: per-driver budgets replace the single global
# one. "Runner stops the offending driver at 100%, warns at 80%; other
# drivers continue unless [routing] leaves no path.")
# --------------------------------------------------------------------------

# Mirrors decision #72's existing cost_model -> agent_spend-unit mapping
# (`_COST_MODEL_TO_SPEND` below, unit half only) -- the same mapping
# `doctor` validates `[agents.<x>.budget].unit` against (v4 section 2:
# "`doctor` errors if `budget.unit` is not producible by that driver's
# `cost_model`"). Not reinvented; see docs/decisions.md.
EXPECTED_BUDGET_UNIT = {"usd": "usd", "tokens": "tokens", "quota": "requests"}


def driver_budget_state(conn, agent_name: str, agent_cfg) -> dict | None:
    """One driver's own spend vs. its own `[agents.<x>.budget]`, summed
    across every project (the budget config is repo-wide, not
    per-project -- `agent_spend` is keyed per-project only because spend
    accrual happens per node, which belongs to a project). `None` if the
    driver has no `budget` configured at all -- unlimited, never
    exhausted, never warned."""
    budget = getattr(agent_cfg, "budget", None)
    if budget is None:
        return None
    spent = conn.execute(
        "SELECT COALESCE(SUM(spent), 0) c FROM agent_spend WHERE agent = ? AND unit = ?",
        (agent_name, budget.unit),
    ).fetchone()["c"]
    limit = budget.limit
    pct = (spent / limit) if limit else 0.0
    return {
        "agent": agent_name,
        "unit": budget.unit,
        "spent": spent,
        "limit": limit,
        "pct": pct,
        "exhausted": bool(limit) and spent >= limit,
        "warn": bool(limit) and pct >= 0.8,
    }


def driver_budget_states(conn, config: MuvueConfig) -> dict[str, dict]:
    """Every configured agent that has a `budget`, keyed by agent name --
    the dashboard "spend vs budget per driver" KPI (plan section 8) and
    `run()`'s final report both read this."""
    return {
        name: state
        for name, cfg in config.agents.items()
        if (state := driver_budget_state(conn, name, cfg)) is not None
    }


def _exhausted_agents(conn, config: MuvueConfig) -> set[str]:
    """Agents whose own budget has hit 100% -- the runner stops
    *scheduling new nodes* to these (v4 section 6), it does not touch
    nodes already in flight."""
    return {
        name
        for name, cfg in config.agents.items()
        if (state := driver_budget_state(conn, name, cfg)) is not None and state["exhausted"]
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
    conn,
    ready_rows,
    config: MuvueConfig,
    *,
    parallel: int,
    agent_override: str | None,
    exhausted_agents: set[str] | None = None,
) -> list[tuple]:
    """Greedily select up to `parallel` ready nodes whose predicted_touches
    are pairwise disjoint, never exceeding any one agent's
    `max_concurrency` within the batch. Skips (does not select) a node
    whose routed agent isn't configured at all, rather than crashing --
    it simply stays `ready` for a human to notice via `muvue status`.

    Note (v4 section 6): `predicted_touches` disjointness is a
    *scheduling heuristic*, not a safety property -- it only ever gets a
    chance to matter (`parallel > 1`) when `core.runner.run` has already
    refused anything but `worktree_mode = "per_node"` for `parallel > 1`
    (`validate_parallel`, called before this). At `parallel == 1` only
    one node is ever selected regardless, so there's nothing to
    "activate" either way.

    v4 section 2/6: a node routed to an agent in `exhausted_agents` (its
    own `[agents.<x>.budget]` is at 100%) is skipped -- the runner stops
    scheduling *new* work to that driver, other drivers keep going. If
    `exhausted_agents` isn't given, it's computed from `config`/`conn`
    (agent_spend vs. each configured budget) so direct callers (tests,
    `select_batch` used standalone) still get real enforcement."""
    if exhausted_agents is None:
        exhausted_agents = _exhausted_agents(conn, config)
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
        if agent_name in exhausted_agents:
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


def _rate_limit_wait_started_at(conn, node_id: int, now: datetime) -> datetime:
    """v4 section 6 / changelog item 11: `max_wait_minutes` bounds how
    long a node may sit `blocked(rate_limit)` under `on_rate_limit =
    "wait"` *in total*, not just since its most recent retry -- a `wait`
    episode spans several block/reconcile/retry cycles (`reconcile_rate_limits`
    un-blocks once `retry_at` passes, the node gets re-tried, and may get
    re-blocked). Finds when *this* episode actually started: the latest
    `runner.rate_limited` event's own `wait_started_at`, carried forward,
    as long as nothing but the reconcile/retry cycle's own `node.ready`/
    `node.start` events happened since -- anything else (a real success,
    a different block reason, a fresh start) means a new episode, so this
    falls back to `now`."""
    latest = conn.execute(
        "SELECT id, ts, payload FROM events WHERE node_id = ? AND type = 'runner.rate_limited' "
        "ORDER BY id DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if latest is not None:
        payload = json.loads(latest["payload"])
        started = payload.get("wait_started_at")
        if started:
            intervening = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE node_id = ? AND id > ? "
                "AND type NOT IN ('node.ready', 'node.start')",
                (node_id, latest["id"]),
            ).fetchone()["c"]
            if intervening == 0:
                return datetime.strptime(started, TS_FORMAT).replace(tzinfo=timezone.utc)
    return now


def _apply_rate_limit(
    conn, node, owner: str, agent_name: str, result: drivers.DriverResult, *, now_fn: Callable[[], datetime] = _now
) -> dict:
    current = now_fn()
    retry_seconds = result.retry_after_seconds or 60
    retry_at = (current + timedelta(seconds=retry_seconds)).strftime(TS_FORMAT)
    wait_started_at = _rate_limit_wait_started_at(conn, node["id"], current)
    with core_db.write_txn(conn):
        nodes_mod.block(
            conn, node["id"], reason="rate_limit", actor=owner, event_actor_role="daemon",
            actor_evidence="subprocess",
        )
        events_mod.record_event(
            conn, project_id=node["project_id"], node_id=node["id"], actor="daemon",
            actor_evidence="subprocess",
            type_="runner.rate_limited",
            payload={
                "agent": agent_name, "retry_at": retry_at, "retry_after_seconds": retry_seconds,
                "wait_started_at": wait_started_at.strftime(TS_FORMAT),
            },
        )
    return {"retry_at": retry_at, "wait_started_at": wait_started_at}


def run_node(
    conn,
    node,
    agent_name: str,
    agent_cfg,
    config: MuvueConfig,
    repo_root,
    *,
    invoke: Callable = drivers.invoke_driver,
    now_fn: Callable[[], datetime] = _now,
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
        return _handle_unavailable(
            conn, node_row, owner, agent_name, agent_cfg, config, repo_root, result, invoke, now_fn=now_fn,
        )

    _record_usage(conn, node["id"], agent_name, result, cost_model=agent_cfg.cost_model)

    if result.status == "rate_limited":
        applied = _apply_rate_limit(conn, node_row, owner, agent_name, result, now_fn=now_fn)
        return _apply_on_rate_limit(
            conn, node_row, agent_name, agent_cfg, config, repo_root,
            applied["retry_at"], applied["wait_started_at"], invoke, now_fn=now_fn,
        )

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


def _dispatch_fallback(conn, node, from_agent, fallback_name, config, repo_root, invoke, now_fn, extra=None):
    fallback_cfg = config.agents.get(fallback_name)
    if fallback_cfg is None:
        return None
    nodes_mod.ready(conn, node["id"], actor="daemon", actor_evidence="subprocess")
    fresh = nodes_mod.get_node(conn, node["id"])
    result = run_node(conn, fresh, fallback_name, fallback_cfg, config, repo_root, invoke=invoke, now_fn=now_fn)
    result["fallback_from"] = from_agent
    if extra:
        result.update(extra)
    return result


def _apply_on_rate_limit(
    conn, node, agent_name, agent_cfg, config, repo_root, retry_at, wait_started_at, invoke,
    *, now_fn: Callable[[], datetime] = _now,
) -> dict:
    """Plan section 6: "runner applies `config.agents.<x>.on_rate_limit`
    (`wait`/`fallback:<agent>`/`pause`)". v4 section 6 / changelog item
    11: when `mode == "wait"`, bounded by `agent_cfg.max_wait_minutes` --
    past that, `on_rate_limit_timeout` applies instead and a notification
    fires (`runner.rate_limit_wait_exhausted`, written via `write_txn` --
    the minimum acceptable "notification fires" per the P5 prompt, since
    no real notification-sending exists yet outside the dashboard; see
    docs/decisions.md)."""
    mode = agent_cfg.on_rate_limit
    if mode.startswith("fallback:"):
        fallback_name = mode.split(":", 1)[1]
        result = _dispatch_fallback(conn, node, agent_name, fallback_name, config, repo_root, invoke, now_fn)
        if result is not None:
            return result
        return {
            "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
            "retry_at": retry_at,
            "error": f"on_rate_limit fallback agent {fallback_name!r} is not configured",
        }

    if mode == "wait":
        elapsed_minutes = (now_fn() - wait_started_at).total_seconds() / 60
        if elapsed_minutes >= agent_cfg.max_wait_minutes:
            timeout_mode = agent_cfg.on_rate_limit_timeout
            with core_db.write_txn(conn):
                events_mod.record_event(
                    conn, project_id=node["project_id"], node_id=node["id"], actor="daemon",
                    actor_evidence="subprocess",
                    type_="runner.rate_limit_wait_exhausted",
                    payload={
                        "agent": agent_name, "waited_minutes": elapsed_minutes,
                        "max_wait_minutes": agent_cfg.max_wait_minutes, "escalated_to": timeout_mode,
                    },
                )
            if timeout_mode.startswith("fallback:"):
                fallback_name = timeout_mode.split(":", 1)[1]
                result = _dispatch_fallback(
                    conn, node, agent_name, fallback_name, config, repo_root, invoke, now_fn,
                    extra={"rate_limit_timeout_escalated": True},
                )
                if result is not None:
                    return result
            return {
                "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
                "retry_at": retry_at, "on_rate_limit": mode,
                "rate_limit_timeout_escalated": True, "on_rate_limit_timeout": timeout_mode,
            }
        return {
            "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
            "retry_at": retry_at, "on_rate_limit": mode,
        }

    # "pause"
    return {
        "node_id": node["id"], "agent": agent_name, "outcome": "blocked_rate_limit",
        "retry_at": retry_at, "on_rate_limit": mode,
    }


def _handle_unavailable(
    conn, node_row, owner, agent_name, agent_cfg, config, repo_root, result, invoke,
    *, now_fn: Callable[[], datetime] = _now,
) -> dict:
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
            res = run_node(conn, fresh, fallback_name, fallback_cfg, config, repo_root, invoke=invoke, now_fn=now_fn)
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


class ParallelismRefused(Exception):
    """v4 section 6 "Parallelism (tightened from v3)": `--parallel N > 1`
    is refused unless `worktree_mode = "per_node"`. Raised before `run`
    opens a DB connection or touches anything -- refusal has zero side
    effects, matching the CLI entry point's own requirement (P5 prompt
    Delta C)."""


def validate_parallel(config: MuvueConfig, parallel: int) -> None:
    if parallel > 1 and config.worktree_mode != "per_node":
        raise ParallelismRefused(
            f"--parallel {parallel} is refused: worktree_mode is "
            f"{config.worktree_mode!r}, not 'per_node'. In 'branch' mode all "
            "agents share one checkout and predicted_touches disjointness does "
            "not prevent same-file races (v4 section 6). Set worktree_mode = "
            "\"per_node\" in config.toml to run with --parallel > 1, or omit "
            "--parallel (default N = 1)."
        )


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
    now_fn: Callable[[], datetime] = _now,
) -> dict:
    """Run every schedulable ready node to a terminal outcome, unattended,
    until there's nothing left to schedule, a driver's own budget leaves
    no routable work, the unit-free `[budget]` stop conditions trip, or a
    node needs human attention (P5 acceptance criteria 1, 2, 6)."""
    validate_parallel(config, parallel)
    repo_root = Path(repo_root)
    parallel = max(1, parallel)

    conn = core_db.connect(db_path)
    try:
        daemon_mod.reconcile_leases(conn)
        reconcile_rate_limits(conn, now=now_fn())
    finally:
        conn.close()

    processed: list[dict] = []
    warned_drivers: set[str] = set()
    paused = None
    cycles = 0
    run_started_at = now_fn()

    while cycles < max_cycles:
        cycles += 1

        # v4 section 2 top-level `[budget]`: unit-free stop conditions,
        # independent of any driver's own budget state.
        if config.budget.max_nodes_per_run and len(processed) >= config.budget.max_nodes_per_run:
            paused = {
                "reason": "max_nodes_per_run",
                "max_nodes_per_run": config.budget.max_nodes_per_run,
                "processed": len(processed),
            }
            break
        elapsed_minutes = (now_fn() - run_started_at).total_seconds() / 60
        if config.budget.max_wall_clock_minutes and elapsed_minutes >= config.budget.max_wall_clock_minutes:
            paused = {
                "reason": "max_wall_clock_minutes",
                "max_wall_clock_minutes": config.budget.max_wall_clock_minutes,
                "elapsed_minutes": elapsed_minutes,
            }
            break

        conn = core_db.connect(db_path)
        try:
            gating = _gating_nodes(conn, project_id)
            if gating:
                paused = {"reason": "nodes_need_attention", "nodes": gating}
                break

            # v4 section 2/6: each driver's own budget, independently.
            # "Runner stops the offending driver at 100% ... other
            # drivers continue unless [routing] leaves no path" -- no
            # separate "no path left" detection is built; an exhausted
            # driver with no alternative simply leaves select_batch with
            # nothing to schedule, which is handled below like any other
            # empty batch.
            exhausted = _exhausted_agents(conn, config)
            for name, state in driver_budget_states(conn, config).items():
                if state["warn"] and name not in warned_drivers:
                    with core_db.write_txn(conn):
                        events_mod.record_event(
                            conn, project_id=None, node_id=None, actor="daemon",
                            actor_evidence="subprocess",
                            type_="runner.driver_budget_warning", payload=state,
                        )
                    warned_drivers.add(name)

            ready_rows = _ready_nodes(conn, project_id)
            batch = select_batch(
                conn, ready_rows, config, parallel=parallel, agent_override=agent_override,
                exhausted_agents=exhausted,
            )
        finally:
            conn.close()

        if not batch:
            break

        def _exec(item):
            node, agent_name, agent_cfg = item
            c = core_db.connect(db_path)
            try:
                return run_node(c, node, agent_name, agent_cfg, config, repo_root, invoke=invoke, now_fn=now_fn)
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
        final_budget = driver_budget_states(conn, config)
    finally:
        conn.close()

    return {"processed": processed, "paused": paused, "cycles": cycles, "budget": final_budget}
