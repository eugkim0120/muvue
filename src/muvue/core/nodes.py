"""Node lifecycle: creation and the status-machine verbs (start/done/fail/...).

Every mutating function here is the single write path (plan working rule 3):
the CLI never issues raw SQL. Each mutation appends a full-row-snapshot event
so `rebuild` (see rebuild.py) can reconstruct live state by replay alone.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import events, state_machine

DEFAULT_LEASE_MINUTES = 60


class NodeError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_node(conn: sqlite3.Connection, node_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise LookupError(f"no such node: {node_id}")
    return row


def create_node(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    kind: str,
    title: str,
    parent_id: int | None = None,
    body_md: str = "",
    criteria: list[str] | None = None,
    criteria_mode: str = "manual",
    risk_tier: str = "low",
    max_attempts: int = 3,
    status: str = "pending",
    actor: str = "human",
) -> sqlite3.Row:
    criteria = criteria or []
    criteria_json = json.dumps(criteria)
    criteria_hash = hashlib.sha256(criteria_json.encode()).hexdigest()
    cur = conn.execute(
        "INSERT INTO nodes (project_id, parent_id, kind, title, body_md, status, "
        "criteria_json, criteria_hash, criteria_mode, risk_tier, max_attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            parent_id,
            kind,
            title,
            body_md,
            status,
            criteria_json,
            criteria_hash,
            criteria_mode,
            risk_tier,
            max_attempts,
        ),
    )
    node_id = cur.lastrowid
    row = get_node(conn, node_id)
    events.record_event(
        conn,
        project_id=project_id,
        node_id=node_id,
        actor=actor,
        type_="node.created",
        payload=dict(row),
    )
    conn.commit()
    return row


def _apply_transition(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    *,
    to_status: str,
    lease_actor: str,
    event_actor_role: str,
    event_type: str,
    request_id: str | None,
    extra_columns: dict | None = None,
    block_reason: str | None = None,
) -> sqlite3.Row:
    """`lease_actor` is the identity checked against nodes.owner (plan
    section 3: "only the lease owner may transition a node"). Distinct from
    `event_actor_role`, which is the events.actor CHECK'd role (plan
    section 3: actor IN {agent, human, hook, daemon})."""
    state_machine.validate_transition(
        node["status"], to_status, owner=node["owner"], actor=lease_actor
    )
    columns = {"status": to_status, "block_reason": block_reason}
    if extra_columns:
        columns.update(extra_columns)
    set_clause = ", ".join(f"{k} = ?" for k in columns)
    conn.execute(
        f"UPDATE nodes SET {set_clause} WHERE id = ?",
        (*columns.values(), node["id"]),
    )
    row = get_node(conn, node["id"])
    events.record_event(
        conn,
        project_id=row["project_id"],
        node_id=row["id"],
        actor=event_actor_role,
        type_=event_type,
        payload=dict(row),
        request_id=request_id,
    )
    return row


def ready(conn: sqlite3.Connection, node_id: int, *, actor: str = "human") -> sqlite3.Row:
    node = get_node(conn, node_id)
    row = _apply_transition(
        conn, node, to_status="ready", lease_actor=node["owner"] or "",
        event_actor_role=actor, event_type="node.ready", request_id=None,
    )
    conn.commit()
    return row


def start(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    actor: str = "agent",
    request_id: str | None = None,
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
) -> dict:
    dup = events.find_recent_by_request_id(conn, request_id, "node.start") if request_id else None
    if dup is not None:
        return {"noop": True, "node": dict(get_node(conn, node_id))}

    node = get_node(conn, node_id)
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    row = _apply_transition(
        conn,
        node,
        to_status="in_progress",
        lease_actor=owner,
        event_actor_role="agent",
        event_type="node.start",
        request_id=request_id,
        extra_columns={"owner": owner, "lease_until": lease_until},
    )
    conn.commit()
    return {"noop": False, "node": dict(row)}


def done(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    request_id: str | None = None,
    summary: str | None = None,
) -> dict:
    """Mark a node done. Idempotent: a node already `done` is a no-op, and a
    duplicate request_id within the 24h dedupe window is a no-op (plan
    section 4 / working rule: request-id idempotency).

    P0 simplification (see docs/decisions.md): drives in_progress -> review
    -> done in a single call, since criteria evaluation and human review
    (Gate machinery) ship in P1.
    """
    node = get_node(conn, node_id)
    if node["status"] == "done":
        return {"noop": True, "node": dict(node)}

    dup = events.find_recent_by_request_id(conn, request_id, "node.done") if request_id else None
    if dup is not None:
        return {"noop": True, "node": dict(get_node(conn, node_id))}

    reviewing = _apply_transition(
        conn, node, to_status="review", lease_actor=owner, event_actor_role="agent",
        event_type="node.review", request_id=None,
    )
    row = _apply_transition(
        conn,
        reviewing,
        to_status="done",
        lease_actor=owner,
        event_actor_role="agent",
        event_type="node.done",
        request_id=request_id,
        extra_columns={"lease_until": None, "summary": summary},
    )
    conn.commit()
    return {"noop": False, "node": dict(row)}


def fail(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    lesson: str,
    trigger: str = "",
    do_instead: str = "",
    scope: str = "",
    request_id: str | None = None,
) -> dict:
    dup = events.find_recent_by_request_id(conn, request_id, "node.fail") if request_id else None
    if dup is not None:
        return {"noop": True, "node": dict(get_node(conn, node_id))}

    node = get_node(conn, node_id)
    attempts = node["attempts"] + 1
    to_status = "failed" if attempts >= node["max_attempts"] else "ready"
    row = _apply_transition(
        conn,
        node,
        to_status=to_status,
        lease_actor=owner,
        event_actor_role="agent",
        event_type="node.fail",
        request_id=request_id,
        extra_columns={
            "attempts": attempts,
            "owner": None if to_status != "in_progress" else owner,
            "lease_until": None,
        },
    )
    lesson_text = json.dumps(
        {"trigger": trigger, "failure": lesson, "do_instead": do_instead, "scope": scope}
    )
    add_note(conn, node_id, kind="lesson", text=lesson_text, actor="agent")
    conn.commit()
    return {"noop": False, "node": dict(row)}


def block(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    reason: str,
    actor: str,
    request_id: str | None = None,
) -> sqlite3.Row:
    if reason not in state_machine.BLOCK_REASONS:
        raise NodeError(f"unknown block reason: {reason!r}")
    node = get_node(conn, node_id)
    row = _apply_transition(
        conn,
        node,
        to_status="blocked",
        lease_actor=actor,
        event_actor_role="agent",
        event_type="node.blocked",
        request_id=request_id,
        block_reason=reason,
    )
    conn.commit()
    return row


def soft_delete(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human"
) -> sqlite3.Row:
    """Soft delete only (plan section 5): nodes with commits attached are
    never hard-deleted; set deleted_at instead."""
    node = get_node(conn, node_id)
    conn.execute(
        "UPDATE nodes SET deleted_at = ? WHERE id = ?", (_now(), node["id"])
    )
    row = get_node(conn, node_id)
    events.record_event(
        conn,
        project_id=row["project_id"],
        node_id=row["id"],
        actor=actor,
        type_="node.deleted",
        payload=dict(row),
    )
    conn.commit()
    return row


def add_note(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    kind: str,
    text: str,
    actor: str = "agent",
    pinned: bool = False,
) -> dict:
    """Notes dedupe by content hash (plan section 4)."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    existing = conn.execute(
        "SELECT * FROM notes WHERE node_id = ? AND content_hash = ?",
        (node_id, content_hash),
    ).fetchone()
    if existing is not None:
        return {"noop": True, "note": dict(existing)}
    cur = conn.execute(
        "INSERT INTO notes (node_id, kind, text, content_hash, pinned) "
        "VALUES (?, ?, ?, ?, ?)",
        (node_id, kind, text, content_hash, int(pinned)),
    )
    note_id = cur.lastrowid
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    events.record_event(
        conn,
        project_id=get_node(conn, node_id)["project_id"],
        node_id=node_id,
        actor=actor,
        type_="note.added",
        payload=dict(row),
    )
    conn.commit()
    return {"noop": False, "note": dict(row)}
