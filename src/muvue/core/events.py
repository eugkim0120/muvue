"""Append-only events log (plan section 3): the single source of truth that
`rebuild` replays to reproduce live DB state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db as db_mod

DEDUPE_WINDOW = timedelta(hours=24)


def record_event(
    conn: sqlite3.Connection,
    *,
    project_id: int | None,
    node_id: int | None,
    actor: str,
    type_: str,
    payload: dict,
    request_id: str | None = None,
    actor_evidence: str | None = None,
) -> int:
    """`actor_evidence` (v4 section 3) records how `actor` was
    determined -- `tty`/`dashboard_token`/`mcp`/`hook`/`subprocess`.
    Purely additive/observational this phase (see docs/decisions.md):
    nothing reads it back to enforce anything yet. Wrapped in its own
    `write_txn` (nesting-safe -- see `core.db.write_txn`) so a direct
    caller of `record_event` alone (there are a couple in tests) still
    gets `BEGIN IMMEDIATE` discipline, not just callers that already
    wrap a bigger operation."""
    with db_mod.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO events (project_id, node_id, actor, actor_evidence, type, "
            "payload, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, node_id, actor, actor_evidence, type_, json.dumps(payload), request_id),
        )
        return cur.lastrowid


def find_recent_by_request_id(
    conn: sqlite3.Connection, request_id: str, type_: str
) -> sqlite3.Row | None:
    """Return the most recent event with this request_id + type within the
    24h dedupe window, or None."""
    if request_id is None:
        return None
    cutoff = (datetime.now(timezone.utc) - DEDUPE_WINDOW).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    row = db_mod.query_one(
        conn,
        "SELECT * FROM events WHERE request_id = ? AND type = ? AND ts >= ? "
        "ORDER BY id DESC LIMIT 1",
        (request_id, type_, cutoff),
    )
    return row


def get_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return db_mod.query_one(conn, "SELECT * FROM events WHERE id = ?", (event_id,))


def ack_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    """`POST /events/{id}/ack` (plan section 8 inbox convention): mark
    one event acknowledged. Pre-v4 this was raw SQL inline in
    `api/app.py` -- a genuine working-rule-3 violation ("If you find
    yourself writing SQL elsewhere, stop") this session's audit caught
    and fixed, see docs/decisions.md. Returns None if `event_id` doesn't
    exist (caller maps that to a 404)."""
    with db_mod.write_txn(conn):
        row = get_event(conn, event_id)
        if row is None:
            return None
        conn.execute(
            "UPDATE events SET acked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (event_id,),
        )
        return get_event(conn, event_id)
