"""Append-only events log (plan section 3): the single source of truth that
`rebuild` replays to reproduce live DB state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

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
) -> int:
    cur = conn.execute(
        "INSERT INTO events (project_id, node_id, actor, type, payload, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, node_id, actor, type_, json.dumps(payload), request_id),
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
    row = conn.execute(
        "SELECT * FROM events WHERE request_id = ? AND type = ? AND ts >= ? "
        "ORDER BY id DESC LIMIT 1",
        (request_id, type_, cutoff),
    ).fetchone()
    return row


def all_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
