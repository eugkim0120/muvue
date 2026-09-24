"""Daemon mechanics (plan section 8): reconcile-on-start (lease expiry),
event-queue processing, and session tokens gating the human-verb API
endpoints.

Holds no in-memory state (plan section 1, principle 1): every function
here takes a fresh `sqlite3.Connection` and reads/writes only through it
or, for the session token, a small file under `<repo>/.muvue/session`.
`core.daemon` is itself part of the single write path -- it never issues
raw SQL that `muvue.core.nodes`/`events` don't already own; reconcile
reuses `nodes._apply_transition` exactly like `nodes.fail` does.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events as events_mod
from . import nodes as nodes_mod

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SESSION_RELPATH = ".muvue/session"
DEFAULT_TOKEN_TTL_MINUTES = 480  # 8h: "short-lived" per plan section 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Reconcile-on-start: expired leases revert to ready (P2 acceptance #2).
# --------------------------------------------------------------------------


def reconcile_leases(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[dict]:
    """Revert every `in_progress` node whose `lease_until` is in the past
    back to `ready` (or `failed` if that exhausts `max_attempts`), with
    `attempts + 1` -- plan section 5 ("Expired leases revert to ready with
    attempts + 1. Daemon reconciles on start"). Returns the list of
    affected node rows (post-transition)."""
    current = now or _now()
    now_s = current.strftime(TS_FORMAT)
    expired = conn.execute(
        "SELECT * FROM nodes WHERE status = 'in_progress' AND lease_until IS NOT NULL "
        "AND lease_until < ? AND deleted_at IS NULL",
        (now_s,),
    ).fetchall()
    reverted = []
    for node in expired:
        attempts = node["attempts"] + 1
        to_status = "failed" if attempts >= node["max_attempts"] else "ready"
        row = nodes_mod._apply_transition(
            conn,
            node,
            to_status=to_status,
            lease_actor=node["owner"] or "",
            event_actor_role="daemon",
            event_type="node.lease_expired",
            request_id=None,
            extra_columns={
                "attempts": attempts,
                "owner": None,
                "lease_until": None,
            },
        )
        reverted.append(dict(row))
    conn.commit()
    return reverted


# --------------------------------------------------------------------------
# Event-queue processing (plan section 8). P2 scope: the mechanism is
# real (drains unacked events into `acked_at`), its consumers are no-ops
# -- anchor-hashing and staleness are P3+/structure-layer work (plan
# section 9). See docs/decisions.md.
# --------------------------------------------------------------------------


def process_queue(conn: sqlite3.Connection, *, batch_size: int = 100) -> int:
    """Drain up to `batch_size` unacked events by marking `acked_at`. No
    consumer logic runs yet (documented no-op, see module docstring)."""
    rows = conn.execute(
        "SELECT id FROM events WHERE acked_at IS NULL ORDER BY id ASC LIMIT ?",
        (batch_size,),
    ).fetchall()
    if not rows:
        return 0
    now_s = _now().strftime(TS_FORMAT)
    ids = [r["id"] for r in rows]
    conn.executemany(
        "UPDATE events SET acked_at = ? WHERE id = ?", [(now_s, i) for i in ids]
    )
    conn.commit()
    return len(ids)


def reconcile_on_start(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """Called once when the daemon (re)starts: reconcile expired leases,
    then drain the queue. This is what makes `muvue serve` restartable
    without losing anything (plan section 1, principle 1)."""
    reverted = reconcile_leases(conn, now=now)
    drained = process_queue(conn)
    return {"reverted_nodes": reverted, "drained_events": drained}


# --------------------------------------------------------------------------
# Session tokens (plan section 2 lists `~/.muvue/session`; P0's
# repo_init.py already gitignores `<repo>/.muvue/session` -- see
# docs/decisions.md for why this module follows that established,
# repo-scoped precedent instead).
# --------------------------------------------------------------------------


def session_path(repo_root: Path) -> Path:
    return Path(repo_root) / SESSION_RELPATH


def create_session(
    repo_root: Path, *, ttl_minutes: int = DEFAULT_TOKEN_TTL_MINUTES, now: datetime | None = None
) -> str:
    """Mint a new session token, overwriting any previous one (only one
    human session is meaningful per repo at a time)."""
    token = secrets.token_urlsafe(32)
    expires_at = ((now or _now()) + timedelta(minutes=ttl_minutes)).strftime(TS_FORMAT)
    path = session_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token, "expires_at": expires_at}))
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform-dependent, not fatal
        pass
    return token


def verify_session(repo_root: Path, token: str | None, *, now: datetime | None = None) -> bool:
    if not token:
        return False
    path = session_path(repo_root)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not secrets.compare_digest(token, data.get("token", "")):
        return False
    try:
        expires_at = _parse(data["expires_at"])
    except (KeyError, ValueError):
        return False
    return (now or _now()) < expires_at
