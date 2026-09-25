"""Plan revisions: append-only, diff-only approval (plan section 3/6).

`replan` may add subtasks within an already-approved task's stated scope
without a new approval. New tasks, deletions, and criteria changes go
through a plan revision instead: `propose_revision` snapshots the current
criteria hash of a set of nodes under revision `n`; `approve_revision`
diffs revision `n` against `n - 1` and re-validates/re-approves only the
nodes that changed. Nodes untouched by the diff keep their existing
`status` and `criteria_hash` (P1 acceptance #5).
"""

from __future__ import annotations

import fnmatch
import hashlib
import sqlite3

from . import db as db_mod
from . import events
from . import nodes as nodes_mod
from .config import MuvueConfig
from .gates import GateError, _require_human, approve_node


def _hash_criteria(criteria_json: str) -> str:
    return hashlib.sha256(criteria_json.encode()).hexdigest()


def replan_add_subtask(
    conn: sqlite3.Connection,
    *,
    parent_task_id: int,
    title: str,
    body_md: str = "",
    criteria: list[str] | None = None,
    depends_on: list[int] | None = None,
    predicted_touches: list[str] | None = None,
    config: MuvueConfig | None = None,
    actor: str = "agent",
    actor_evidence: str = "tty",
) -> dict:
    """Add a subtask under an approved task. Plan section 6: `replan` may
    add subtasks "within an approved task's stated scope without
    approval". Inside scope means every predicted touch falls under one
    of the parent's `predicted_touches` globs, and the parent has fewer
    than `planning.max_subtasks` subtasks. Such a subtask is created
    `ready`. Anything else is created `pending`, which needs `approve
    task:ID`, and a `replan.gated` event records why."""
    max_subtasks = (config or MuvueConfig()).planning.max_subtasks
    with db_mod.write_txn(conn):
        parent = nodes_mod.get_node(conn, parent_task_id)
        if parent["kind"] != "task":
            raise GateError(f"node {parent_task_id} is kind={parent['kind']!r}, not a task")
        if parent["criteria_hash"] is None:
            raise GateError(
                f"task {parent_task_id} has not been Gate 2 approved yet; "
                "replan cannot add subtasks to an unapproved task"
            )
        parent_globs = [
            r["path_glob"]
            for r in conn.execute(
                "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (parent_task_id,)
            )
        ]
        outside = [
            t for t in predicted_touches or []
            if not any(fnmatch.fnmatch(t, g) for g in parent_globs)
        ]
        existing = conn.execute(
            "SELECT COUNT(*) c FROM nodes WHERE parent_id = ? AND kind = 'subtask' "
            "AND deleted_at IS NULL",
            (parent_task_id,),
        ).fetchone()["c"]
        reasons = []
        if outside:
            reasons.append(f"touches outside the parent's predicted_touches: {outside}")
        if existing >= max_subtasks:
            reasons.append(f"parent already has {existing} subtasks (max_subtasks={max_subtasks})")

        row = nodes_mod.create_node(
            conn,
            project_id=parent["project_id"],
            parent_id=parent_task_id,
            kind="subtask",
            title=title,
            body_md=body_md,
            criteria=criteria,
            depends_on=depends_on,
            predicted_touches=predicted_touches,
            status="pending" if reasons else "ready",
            actor=actor,
            actor_evidence=actor_evidence,
        )
        if reasons:
            events.record_event(
                conn, project_id=parent["project_id"], node_id=row["id"], actor=actor,
                actor_evidence=actor_evidence, type_="replan.gated",
                payload={"parent_task_id": parent_task_id, "reason": "; ".join(reasons)},
            )
        return dict(row)


def propose_revision(
    conn: sqlite3.Connection,
    project_id: int,
    node_ids: list[int],
    *,
    actor: str = "agent",
    actor_evidence: str = "tty",
) -> dict:
    with db_mod.write_txn(conn):
        prev = conn.execute(
            "SELECT MAX(n) m FROM plan_revisions WHERE project_id = ?", (project_id,)
        ).fetchone()["m"]
        n = (prev or 0) + 1
        cur = conn.execute(
            "INSERT INTO plan_revisions (project_id, n) VALUES (?, ?)", (project_id, n)
        )
        revision_id = cur.lastrowid
        for node_id in node_ids:
            node = nodes_mod.get_node(conn, node_id)
            conn.execute(
                "INSERT INTO plan_revision_nodes (revision_id, node_id, criteria_hash) "
                "VALUES (?, ?, ?)",
                (revision_id, node_id, _hash_criteria(node["criteria_json"])),
            )
        events.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="revision.proposed",
            payload={"n": n, "node_ids": list(node_ids)},
        )
        return {"id": revision_id, "n": n}


def _revision_snapshot(conn: sqlite3.Connection, project_id: int, n: int) -> dict[int, str]:
    """{node_id: criteria_hash} snapshot for revision n, or {} if n < 1."""
    if n < 1:
        return {}
    row = db_mod.query_one(
        conn,
        "SELECT id FROM plan_revisions WHERE project_id = ? AND n = ?",
        (project_id, n),
    )
    if row is None:
        return {}
    rows = db_mod.query_all(
        conn,
        "SELECT node_id, criteria_hash FROM plan_revision_nodes WHERE revision_id = ?",
        (row["id"],),
    )
    return {r["node_id"]: r["criteria_hash"] for r in rows}


def diff_revision(conn: sqlite3.Connection, project_id: int, n: int) -> dict:
    """Diff revision n against n - 1: which nodes were added, removed,
    changed (criteria hash differs), or left unchanged."""
    current = _revision_snapshot(conn, project_id, n)
    previous = _revision_snapshot(conn, project_id, n - 1)
    added = sorted(nid for nid in current if nid not in previous)
    removed = sorted(nid for nid in previous if nid not in current)
    changed = sorted(
        nid for nid in current if nid in previous and current[nid] != previous[nid]
    )
    unchanged = sorted(
        nid for nid in current if nid in previous and current[nid] == previous[nid]
    )
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def approve_revision(
    conn: sqlite3.Connection,
    project_id: int,
    n: int,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
    config: MuvueConfig | None = None,
) -> dict:
    _require_human(actor, actor_evidence)
    with db_mod.write_txn(conn):
        revision = conn.execute(
            "SELECT * FROM plan_revisions WHERE project_id = ? AND n = ?", (project_id, n)
        ).fetchone()
        if revision is None:
            raise GateError(f"no plan revision n={n} for project {project_id}")
        if revision["approved_at"] is not None:
            return {"noop": True, "n": n}

        diff = diff_revision(conn, project_id, n)
        touched: list[int] = []
        for node_id in diff["added"] + diff["changed"]:
            approve_node(conn, node_id, actor=actor, actor_evidence=actor_evidence, config=config)
            touched.append(node_id)
        for node_id in diff["removed"]:
            nodes_mod.soft_delete(conn, node_id, actor=actor, actor_evidence=actor_evidence)
            touched.append(node_id)

        conn.execute(
            "UPDATE plan_revisions SET approved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?",
            (revision["id"],),
        )
        events.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="revision.approved",
            payload={"n": n, "diff": diff, "touched_node_ids": touched},
        )
        return {"noop": False, "n": n, "diff": diff, "touched_node_ids": touched}
