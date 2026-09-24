"""Gate 1 / Gate 2 approval, criteria freeze, granularity lint (plan
section 5). Every mutation here goes through the single write path: raw SQL
stays inside `muvue.core`, never in the CLI, and every mutating function
opens its writes inside `core.db.write_txn`.
"""

from __future__ import annotations

import hashlib
import sqlite3

from . import db as db_mod
from . import events
from . import nodes as nodes_mod
from . import projects as projects_mod
from . import risk as risk_mod
from .config import MuvueConfig


class GateError(Exception):
    pass


class HumanOnly(GateError):
    """Raised when a non-human actor calls a human verb (plan section 4:
    "Never exposed over MCP" for approve/reject/gate2/revision approval).
    Enforced here, in `muvue.core`, not only at the CLI/API/MCP surface --
    P3 acceptance #4 requires an adversarial agent script that calls a
    human verb directly against core to be refused by core itself."""


def _require_human(actor: str) -> None:
    if actor != "human":
        raise HumanOnly(
            f"only a human may perform this action (actor was {actor!r}); "
            "human verbs are never exposed over MCP (plan section 4)"
        )


def _hash_criteria(criteria_json: str) -> str:
    return hashlib.sha256(criteria_json.encode()).hexdigest()


def submit_spec(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    body_md: str,
    actor: str = "agent",
    actor_evidence: str = "tty",
) -> dict:
    """Gate 1: agent writes a spec node. Created `pending`; a human must
    `approve_spec` before decomposition into tasks."""
    row = nodes_mod.create_node(
        conn,
        project_id=project_id,
        kind="spec",
        title=title,
        body_md=body_md,
        status="pending",
        actor=actor,
        actor_evidence=actor_evidence,
    )
    return dict(row)


def approve_spec(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> dict:
    """Gate 1 approval: pending -> ready. Human-only, enforced here (see
    `_require_human`/`HumanOnly`) -- not only at the CLI layer."""
    _require_human(actor)
    with db_mod.write_txn(conn):
        node = nodes_mod.get_node(conn, node_id)
        if node["kind"] != "spec":
            raise GateError(f"node {node_id} is kind={node['kind']!r}, not a spec")
        row = nodes_mod.ready(conn, node_id, actor=actor, actor_evidence=actor_evidence)
        return dict(row)


def lint_task(conn: sqlite3.Connection, node: sqlite3.Row, config: MuvueConfig) -> list[str]:
    """Granularity lint (plan section 5, P1 acceptance #3). Warns only,
    never blocks approval."""
    warnings: list[str] = []
    touches = conn.execute(
        "SELECT COUNT(*) c FROM predicted_touches WHERE node_id = ?", (node["id"],)
    ).fetchone()["c"]
    if touches > config.planning.max_files_per_task:
        warnings.append(
            f"node {node['id']} predicts touching {touches} files "
            f"(max_files_per_task={config.planning.max_files_per_task})"
        )
    subtasks = conn.execute(
        "SELECT COUNT(*) c FROM nodes WHERE parent_id = ? AND kind = 'subtask' "
        "AND deleted_at IS NULL",
        (node["id"],),
    ).fetchone()["c"]
    if subtasks > config.planning.max_subtasks:
        warnings.append(
            f"node {node['id']} has {subtasks} subtasks "
            f"(max_subtasks={config.planning.max_subtasks})"
        )
    if node["criteria_mode"] != "auto":
        warnings.append(
            f"node {node['id']} lacks an auto criterion "
            f"(criteria_mode={node['criteria_mode']!r})"
        )
    return warnings


def approve_node(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
    config: MuvueConfig | None = None,
) -> dict:
    """Freeze `criteria_json` into `criteria_hash` and move the node
    `pending -> ready`. Used for the initial Gate 2 approval of a task node
    and for re-approval after a post-freeze criteria edit (both are the
    same operation: freeze the current criteria, unblock `start`)."""
    _require_human(actor)
    with db_mod.write_txn(conn):
        node = nodes_mod.get_node(conn, node_id)
        if node["status"] != "pending":
            raise GateError(
                f"node {node_id} is status={node['status']!r}, not pending approval"
            )
        warnings = (
            lint_task(conn, node, config)
            if config is not None and node["kind"] in ("task", "subtask")
            else []
        )
        # Initial freeze only (criteria_hash was NULL): compute the diff-signal
        # risk tier here, once (plan section 5's tier inputs -- see
        # core.risk). A *re*-approval (criteria_hash already set, i.e. this
        # node was frozen once before and is being re-approved after
        # core.gates.edit_criteria pulled it back to pending) must never
        # recompute tier here -- edit_criteria already forced it to `high`,
        # and P2 acceptance #4 requires that a criteria edit is never
        # auto-approved back down by a later recompute (docs/decisions.md).
        if node["criteria_hash"] is None and config is not None and node["kind"] in ("task", "subtask"):
            tier = risk_mod.compute_tier(conn, node, config)
            conn.execute("UPDATE nodes SET risk_tier = ? WHERE id = ?", (tier, node_id))
            node = nodes_mod.get_node(conn, node_id)
        frozen_hash = _hash_criteria(node["criteria_json"])
        conn.execute("UPDATE nodes SET criteria_hash = ? WHERE id = ?", (frozen_hash, node_id))
        row = nodes_mod.ready(conn, node_id, actor=actor, actor_evidence=actor_evidence)
        return {"node": dict(row), "warnings": warnings}


def approve_gate2(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    config: MuvueConfig,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> dict:
    """Approve every pending task/subtask node decomposed under `project_id`,
    freezing each one's criteria and running the granularity lint. On
    success flips `project.phase` from `planning` to `executing`, which is
    what `start` gates on (P1 acceptance #1)."""
    _require_human(actor)
    with db_mod.write_txn(conn):
        pending = conn.execute(
            "SELECT * FROM nodes WHERE project_id = ? AND kind IN ('task', 'subtask') "
            "AND status = 'pending' AND deleted_at IS NULL",
            (project_id,),
        ).fetchall()
        if not pending:
            raise GateError(f"project {project_id} has no pending task nodes for Gate 2")

        warnings: dict[int, list[str]] = {}
        approved_ids: list[int] = []
        for node in pending:
            result = approve_node(conn, node["id"], actor=actor, actor_evidence=actor_evidence, config=config)
            approved_ids.append(node["id"])
            if result["warnings"]:
                warnings[node["id"]] = result["warnings"]

        events.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="gate2.approved",
            payload={"approved_node_ids": approved_ids, "warnings": warnings},
        )
        # Records its own project.phase_changed event (rebuild replays events
        # whose type starts with "project." to reconstruct the projects table;
        # gate2.approved above is metadata only, not a project-row snapshot).
        projects_mod.set_phase(conn, project_id, "executing", actor=actor, actor_evidence=actor_evidence)
        return {"approved_node_ids": approved_ids, "warnings": warnings}


def edit_criteria(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    criteria_json: str,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> dict:
    """Edit a node's acceptance criteria. If the node was already frozen
    (has a `criteria_hash` from a prior Gate 2 / revision approval) and the
    new criteria hash differs, bump `risk_tier` to `high` and pull the node
    back to `pending` so `start` is refused until a human re-approves it
    via `approve_node` (P1 acceptance #2)."""
    with db_mod.write_txn(conn):
        node = nodes_mod.get_node(conn, node_id)
        if node["status"] not in ("ready", "pending"):
            raise GateError(
                f"cannot edit criteria on node {node_id} while status={node['status']!r}"
            )
        new_hash = _hash_criteria(criteria_json)
        was_frozen = node["criteria_hash"] is not None
        changed = was_frozen and new_hash != node["criteria_hash"]

        conn.execute("UPDATE nodes SET criteria_json = ? WHERE id = ?", (criteria_json, node_id))
        # P3 acceptance #2: a criteria edit is a human-visible edit regardless
        # of whether it changes the frozen hash, so it bumps `nodes.version`
        # -- an agent mid-`in_progress` that captured `version` at `start` and
        # later calls `done(expected_version=...)` must see the mismatch.
        nodes_mod.bump_version(conn, node_id, actor=actor, actor_evidence=actor_evidence)
        if changed:
            # One source of truth for "criteria edit forces high" (plan
            # section 5): routes through core.risk instead of special-casing
            # the literal here, per the P2 prompt's formalization requirement.
            forced_tier = risk_mod.compute_tier(conn, node, MuvueConfig(), criteria_edited=True)
            conn.execute("UPDATE nodes SET risk_tier = ? WHERE id = ?", (forced_tier, node_id))

        row = nodes_mod.get_node(conn, node_id)
        events.record_event(
            conn,
            project_id=row["project_id"],
            node_id=node_id,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="node.criteria_edited",
            payload={"node": dict(row), "re_approval_required": changed},
        )

        if changed and row["status"] == "ready":
            row = nodes_mod.to_pending(conn, node_id, actor=actor, actor_evidence=actor_evidence)

        return {"node": dict(row), "re_approval_required": changed}
