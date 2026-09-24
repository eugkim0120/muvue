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

from . import events, review, risk, state_machine

DEFAULT_LEASE_MINUTES = 60


class NodeError(Exception):
    pass


class HumanOnly(NodeError):
    """Raised when a non-human actor calls a human verb (plan section 4:
    "Never exposed over MCP"). `core.gates` has its own identically-named
    exception for the same rule applied to gate/revision approvals; this
    one covers review approve/reject, which live in `nodes.py` to avoid a
    `nodes` <-> `gates` import cycle. Enforced in `muvue.core` itself
    (P3 acceptance #4: an adversarial agent script calling a human verb
    directly against core must be refused by core, not just by the
    CLI/API/MCP surface)."""


def _require_human(actor: str) -> None:
    if actor != "human":
        raise HumanOnly(
            f"only a human may perform this action (actor was {actor!r}); "
            "human verbs are never exposed over MCP (plan section 4)"
        )


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
    predicted_touches: list[str] | None = None,
) -> sqlite3.Row:
    criteria = criteria or []
    criteria_json = json.dumps(criteria)
    # criteria_hash stays NULL until Gate 2 (or a plan-revision re-approval)
    # freezes it -- see core.gates.approve_node. A non-NULL criteria_hash
    # means "this criteria_json was approved"; that's the signal
    # core.gates.edit_criteria uses to detect a post-freeze edit.
    cur = conn.execute(
        "INSERT INTO nodes (project_id, parent_id, kind, title, body_md, status, "
        "criteria_json, criteria_mode, risk_tier, max_attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            parent_id,
            kind,
            title,
            body_md,
            status,
            criteria_json,
            criteria_mode,
            risk_tier,
            max_attempts,
        ),
    )
    node_id = cur.lastrowid
    for glob in predicted_touches or []:
        conn.execute(
            "INSERT OR IGNORE INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
            (node_id, glob),
        )
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


def to_pending(conn: sqlite3.Connection, node_id: int, *, actor: str = "human") -> sqlite3.Row:
    """ready -> pending. Used by Gate 2 criteria-edit re-tiering (P1): a
    frozen node whose criteria change is pulled back out of `ready` until a
    human re-approves it (see core.gates.edit_criteria)."""
    node = get_node(conn, node_id)
    row = _apply_transition(
        conn, node, to_status="pending", lease_actor=node["owner"] or "",
        event_actor_role=actor, event_type="node.pending", request_id=None,
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
    project = conn.execute(
        "SELECT phase FROM projects WHERE id = ?", (node["project_id"],)
    ).fetchone()
    if project is not None and project["phase"] in ("planning", "paused"):
        raise NodeError(
            f"cannot start node {node_id}: project {node['project_id']} is "
            f"{project['phase']} (Gate 2 not yet approved, or paused -- plan "
            "section 5 'Emergency stop': pause refuses start)"
        )
    # P3 acceptance #1: a second driver cannot take an already-leased node.
    # `state_machine.TRANSITIONS` already refuses a second `start` once the
    # node is `in_progress` (there is no (in_progress, in_progress) edge),
    # but that raises a generic InvalidTransition regardless of *why*. This
    # explicit, earlier check gives a precise "leased by someone else"
    # error for the common case -- a different owner racing the same
    # `ready` node, or calling `start` again before the daemon's
    # reconcile-on-start has reverted an expired lease -- instead of
    # silently reassigning the lease.
    if (
        node["status"] == "in_progress"
        and node["owner"] is not None
        and node["owner"] != owner
        and node["lease_until"] is not None
        and node["lease_until"] > datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ):
        raise NodeError(
            f"cannot start node {node_id}: leased by {node['owner']!r} until "
            f"{node['lease_until']} (refused, not reassigned)"
        )
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


def bump_version(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human"
) -> sqlite3.Row:
    """Optimistic concurrency counter (plan section 3: `nodes.version`).
    Called on human-visible edits made to a node while it may be leased
    out to an agent (`add_note` for human notes/comments,
    `core.gates.edit_criteria` for criteria changes) so a lease holder's
    later `done(..., expected_version=...)` can detect "this node changed
    under me" instead of silently overwriting a human's edit (P3
    acceptance #2)."""
    conn.execute("UPDATE nodes SET version = version + 1 WHERE id = ?", (node_id,))
    row = get_node(conn, node_id)
    events.record_event(
        conn,
        project_id=row["project_id"],
        node_id=node_id,
        actor=actor,
        type_="node.version_bumped",
        payload=dict(row),
    )
    return row


class VersionMismatch(NodeError):
    """Raised by `done`/`fail` when `expected_version` no longer matches
    `nodes.version` -- the node was edited (e.g. a human note or criteria
    change) since the caller's `start` (P3 acceptance #2)."""


def done(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    request_id: str | None = None,
    summary: str | None = None,
    config=None,
    expected_version: int | None = None,
    run_checks=None,
    cwd: str = ".",
) -> dict:
    """Mark a node done. Idempotent: a node already `done` is a no-op, and a
    duplicate request_id within the 24h dedupe window is a no-op (plan
    section 4 / working rule: request-id idempotency).

    P0 drove in_progress -> review -> done unconditionally in one call
    (criteria evaluation and human review were P1/P2 scope). P2 (plan
    section 5, "done -> review") adds real gating, but only when `config`
    is passed: with `config=None` (every P0/P1 call site, still exercised
    by tests/test_idempotency.py and tests/test_rebuild_property.py), the
    old unconditional in_progress -> review -> done behavior is preserved
    byte-for-byte -- see docs/decisions.md. When `config` is given (the
    CLI and API do, since both load config on every invocation), the node
    is routed through core.risk: low-tier and unflagged auto-approves
    straight to `done`; anything else (medium/high tier, or a diff that
    touches test files) stops at `review` and needs a human
    `nodes.approve_review`.
    """
    node = get_node(conn, node_id)
    if node["status"] == "done":
        return {"noop": True, "node": dict(node)}

    dup = events.find_recent_by_request_id(conn, request_id, "node.done") if request_id else None
    if dup is not None:
        return {"noop": True, "node": dict(get_node(conn, node_id))}

    if expected_version is not None and node["version"] != expected_version:
        raise VersionMismatch(
            f"cannot mark node {node_id} done: version is {node['version']}, "
            f"expected {expected_version} (edited since start -- re-brief "
            "and retry rather than overwrite)"
        )

    reviewing = _apply_transition(
        conn, node, to_status="review", lease_actor=owner, event_actor_role="agent",
        event_type="node.review", request_id=None,
    )

    if config is None:
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
        return {"noop": False, "node": dict(row), "auto_approved": True}

    tier = risk.max_tier(risk.compute_tier(conn, reviewing, config), reviewing["risk_tier"])
    flagged = risk.is_flagged(conn, reviewing, config)
    conn.execute("UPDATE nodes SET risk_tier = ? WHERE id = ?", (tier, node_id))
    reviewing = get_node(conn, node_id)

    # Light-mode `review` dispatch (plan section 5, P3): auto/external/
    # manual criteria modes each add their own reason to flag a node to
    # `review`, on top of core.risk's tier/test-touch flag.
    dispatch = review.dispatch(conn, reviewing, config, run_checks=run_checks, cwd=cwd)
    if dispatch["flag"]:
        flagged = True
        events.record_event(
            conn, project_id=reviewing["project_id"], node_id=node_id, actor="agent",
            type_=dispatch["event_type"], payload=dispatch["payload"],
        )

    if tier == "low" and not flagged:
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
        events.record_event(
            conn,
            project_id=row["project_id"],
            node_id=node_id,
            actor="daemon",
            type_="review.auto_approved",
            payload={"tier": tier},
        )
        conn.commit()
        return {"noop": False, "node": dict(row), "auto_approved": True}

    conn.execute("UPDATE nodes SET summary = ? WHERE id = ?", (summary, node_id))
    row = get_node(conn, node_id)
    events.record_event(
        conn,
        project_id=row["project_id"],
        node_id=node_id,
        actor="agent",
        type_="review.awaiting",
        payload={"tier": tier, "flagged": flagged},
        request_id=request_id,
    )
    conn.commit()
    return {"noop": False, "node": dict(row), "auto_approved": False}


def approve_review(conn: sqlite3.Connection, node_id: int, *, actor: str = "human") -> dict:
    """Human approval of a node sitting in `review` (plan section 5,
    "done -> review... manual waits for a human"). Transitions
    review -> done. The state machine's `review -> done` edge requires the
    actor to match `nodes.owner` (the agent's lease) -- a human approver is
    verified by the dashboard/API session token instead (see
    core.daemon.auth), so this passes the node's own `owner` as the
    lease-check identity while recording the *event* under the real actor
    (`actor`, default "human"). Same pattern `core.gates.approve_spec` /
    `approve_node` already use via `nodes.ready`.

    Refuses (`HumanOnly`) if `actor != "human"`.

    Logs a `metric.rubber_stamp` event when the elapsed time since the
    node entered `review` is under 10 seconds (plan section 5: "Time-to-
    approve under 10s is logged as a rubber-stamp signal")."""
    _require_human(actor)
    node = get_node(conn, node_id)
    if node["status"] != "review":
        raise NodeError(f"node {node_id} is status={node['status']!r}, not in review")
    review_event = conn.execute(
        "SELECT ts FROM events WHERE node_id = ? AND type = 'node.review' "
        "ORDER BY id DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    row = _apply_transition(
        conn,
        node,
        to_status="done",
        lease_actor=node["owner"] or "",
        event_actor_role=actor,
        event_type="node.done",
        request_id=None,
        extra_columns={"lease_until": None},
    )
    if review_event is not None:
        entered = datetime.strptime(review_event["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
        elapsed = (datetime.now(timezone.utc) - entered).total_seconds()
        if elapsed < 10:
            events.record_event(
                conn,
                project_id=row["project_id"],
                node_id=node_id,
                actor=actor,
                type_="metric.rubber_stamp",
                payload={"elapsed_seconds": elapsed},
            )
    conn.commit()
    return {"node": dict(row)}


def reject_review(
    conn: sqlite3.Connection, node_id: int, *, feedback: str, actor: str = "human"
) -> dict:
    """Human rejection of a node in `review` (plan section 4 human verb
    `reject --feedback`): review -> in_progress, feedback recorded as a
    `feedback` note so the agent picks it up on its next `brief`/`show`.
    Refuses (`HumanOnly`) if `actor != "human"`."""
    _require_human(actor)
    node = get_node(conn, node_id)
    if node["status"] != "review":
        raise NodeError(f"node {node_id} is status={node['status']!r}, not in review")
    row = _apply_transition(
        conn,
        node,
        to_status="in_progress",
        lease_actor=node["owner"] or "",
        event_actor_role=actor,
        event_type="node.rejected",
        request_id=None,
    )
    add_note(conn, node_id, kind="feedback", text=feedback, actor=actor)
    conn.commit()
    return {"node": dict(row)}


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
    expected_version: int | None = None,
) -> dict:
    dup = events.find_recent_by_request_id(conn, request_id, "node.fail") if request_id else None
    if dup is not None:
        return {"noop": True, "node": dict(get_node(conn, node_id))}

    node = get_node(conn, node_id)
    if expected_version is not None and node["version"] != expected_version:
        raise VersionMismatch(
            f"cannot fail node {node_id}: version is {node['version']}, "
            f"expected {expected_version} (edited since start)"
        )
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
    if actor == "human":
        # Plan section 3/5: a human-visible edit made while a node may be
        # leased out bumps `nodes.version`, so the lease holder's `done`
        # can detect it (P3 acceptance #2). Agent-authored notes (lessons,
        # discoveries the agent itself records) are not edits *by someone
        # else* and don't bump it.
        bump_version(conn, node_id, actor=actor)
    conn.commit()
    return {"noop": False, "note": dict(row)}
