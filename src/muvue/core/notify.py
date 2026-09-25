"""`[notify] url` (plan v4 section 2: "ntfy topic or webhook").

`flush` POSTs every inbox-worthy event recorded since the last flush to
the configured URL, as plain text with one line per event, e.g.

    T12 review.awaiting (tier medium)
    T14 question.asked "keep the v1 reader?"

Plain text is what an ntfy topic displays as-is. A generic webhook
receives the same body, and the `X-Muvue-Event-Ids` header lists the
event ids it covers.

Delivery is at most once. The cursor (the latest `notify.sent` or
`notify.failed` event's `up_to`) advances even when the POST fails, and
the failure is recorded as `notify.failed`. Retrying would mean an
endpoint that is down gets hit every drain tick. The first flush after
a URL is configured starts from the current end of the log, so enabling
notifications doesn't replay history.

Called by the daemon's background loop and by `muvue run` after each
cycle, never inside a write transaction (network I/O).
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import urllib.request
from typing import Callable

from . import db as db_mod
from . import events as events_mod
from .config import MuvueConfig

TIMEOUT_S = 5
BATCH_LIMIT = 50

NOTIFY_TYPES = (
    "question.asked",
    "review.awaiting",
    "node.blocked",
    "node.fail",
    "node.awaiting_approval",
    "merge.conflict",
    "replan.gated",
    "runner.rate_limit_wait_exhausted",
    "runner.driver_budget_warning",
    "runner.start_failed",
    "unattributed_commit",
    "inbox.unattributed_commit",
    "inbox.audit_drift_signal",
)

Post = Callable[[str, bytes, dict], None]


def _post(url: str, body: bytes, headers: dict) -> None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        response.read()


def _line(event: sqlite3.Row) -> str | None:
    try:
        payload = json.loads(event["payload"] or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    etype = event["type"]
    if etype == "node.fail" and payload.get("status") != "failed":
        return None  # a retry, not a terminal failure
    who = f"T{event['node_id']}" if event["node_id"] else f"P{event['project_id'] or '-'}"
    detail = ""
    if etype == "question.asked":
        detail = " " + json.dumps(payload.get("text", ""))
    elif etype == "review.awaiting":
        detail = f" (tier {payload.get('tier')})"
    elif etype == "node.blocked":
        detail = f" ({payload.get('block_reason')})"
    elif etype in ("replan.gated", "runner.start_failed"):
        detail = " " + json.dumps(payload.get("reason") or payload.get("error") or "")
    elif etype == "runner.rate_limit_wait_exhausted":
        detail = f" ({payload.get('agent')}, escalated to {payload.get('escalated_to')})"
    return f"{who} {etype}{detail}"


def _cursor(conn: sqlite3.Connection) -> int | None:
    row = db_mod.query_one(
        conn,
        "SELECT payload FROM events WHERE type IN ('notify.sent', 'notify.failed') "
        "ORDER BY id DESC LIMIT 1",
    )
    return None if row is None else json.loads(row["payload"])["up_to"]


def _record(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    with db_mod.write_txn(conn):
        events_mod.record_event(
            conn, project_id=None, node_id=None, actor="daemon", actor_evidence="subprocess",
            type_=type_, payload=payload,
        )


def flush(conn: sqlite3.Connection, config: MuvueConfig, *, post: Post = _post) -> dict:
    """Send what's new. Returns `{"sent": n}` (0 when no URL is set)."""
    url = config.notify.url.strip()
    if not url:
        return {"sent": 0}
    cursor = _cursor(conn)
    latest = db_mod.query_one(conn, "SELECT COALESCE(MAX(id), 0) m FROM events")["m"]
    if cursor is None:
        _record(conn, "notify.sent", {"up_to": latest, "count": 0, "initial": True})
        return {"sent": 0}
    placeholders = ", ".join("?" for _ in NOTIFY_TYPES)
    rows = db_mod.query_all(
        conn,
        f"SELECT id, type, node_id, project_id, payload FROM events WHERE id > ? AND id <= ? "
        f"AND type IN ({placeholders}) ORDER BY id LIMIT ?",
        (cursor, latest, *NOTIFY_TYPES, BATCH_LIMIT),
    )
    # A full batch may not reach `latest`; resume after its last row.
    up_to = rows[-1]["id"] if len(rows) == BATCH_LIMIT else latest
    if not rows:
        # Nothing notifiable. Don't record a cursor move: that record
        # would itself be a new event, and every tick would write one.
        return {"sent": 0}
    lines = [(r["id"], line) for r in rows if (line := _line(r)) is not None]
    if not lines:
        _record(conn, "notify.sent", {"up_to": up_to, "count": 0})
        return {"sent": 0}
    body = ("\n".join(line for _, line in lines) + "\n").encode()
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": f"muvue: {len(lines)} item(s) need attention",
        "X-Muvue-Event-Ids": ",".join(str(i) for i, _ in lines),
    }
    try:
        post(url, body, headers)
    except (OSError, http.client.HTTPException) as e:
        # URLError, HTTPError and timeouts are OSErrors; a malformed
        # response raises http.client.HTTPException.
        _record(conn, "notify.failed", {"up_to": up_to, "count": len(lines), "error": str(e)})
        return {"sent": 0, "error": str(e)}
    _record(conn, "notify.sent", {"up_to": up_to, "count": len(lines)})
    return {"sent": len(lines)}
