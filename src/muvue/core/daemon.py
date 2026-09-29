"""Daemon mechanics (plan section 8): reconcile-on-start (lease expiry,
hook-spool drain), and the in-memory session token that gates
mutating API endpoints (plan v4 section 8a, control 5).

Holds no *reconstructible* state outside the DB (plan section 1,
principle 1) -- except the session token itself, which v4 section 8a
explicitly requires to live in memory only: "Nothing token-shaped is
written to disk. v3's `~/.muvue/session` file is removed: an agent can
read it and acquire every human verb." `SessionManager` is therefore a
deliberate, documented exception to "no in-memory daemon state": it is
*not* reconstructible from the DB by design (a restart must mint a
*new* token -- control 6, "token rotates on `serve` restart"), and it
must never be persisted anywhere a same-user process could read it
un-authenticated (a file, the DB, a log line). See docs/threat-model.md
and docs/decisions.md #84.

`core.daemon` is itself part of the single write path for everything
else -- it never issues raw SQL that `muvue.core.nodes`/`events` don't
already own; reconcile reuses `nodes._apply_transition` exactly like
`nodes.fail` does.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from . import db as db_mod
from . import hooks as hooks_mod
from . import nodes as nodes_mod

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
DEFAULT_IDLE_TIMEOUT_MINUTES = 480  # 8h idle expiry, v4 section 8a control 6
AuthStatus = Literal["ok", "missing", "invalid", "expired"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Reconcile-on-start: expired leases revert to ready (P2 acceptance #2).
# --------------------------------------------------------------------------


def port_file_path(repo_root: Path) -> Path:
    """v4 section 2: `~/.muvue/daemon/<repo-hash>.json`, one per repo."""
    from .strict import _repo_hash

    return Path.home() / ".muvue" / "daemon" / f"{_repo_hash(repo_root)}.json"


def write_port_file(repo_root: Path, *, port: int, pid: int) -> Path:
    """Port and PID only -- the session token never touches disk (v4
    section 8a control 5). Created 0600 from the start, not chmod-ed
    after, so there is no window where it is world-readable."""
    path = port_file_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"port": port, "pid": pid}, f)
    os.replace(tmp, path)
    return path


def remove_port_file(repo_root: Path, *, pid: int) -> None:
    """Remove the port file only if it still names this daemon, so a
    second daemon that took over the repo keeps its own file."""
    path = port_file_path(repo_root)
    try:
        owner = json.loads(path.read_text()).get("pid")
    except FileNotFoundError:
        return
    if owner == pid:
        path.unlink()


def reconcile_leases(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[dict]:
    """Revert every `in_progress` node whose `lease_until` is in the past
    back to `ready`, with `lease_expiries + 1` -- v4 section 3/5:
    "`lease_expiries` counts crash/timeout reclaims and never drives
    `failed`. (v3 conflated them, so a daemon restart could burn a
    node's retries.)" `attempts` (the *agent*-failure counter `core.
    nodes.fail` owns) is left untouched here -- a lease expiry is never
    routed to `failed`, regardless of how many times it's happened, so
    there is no `max_attempts` check on this path at all. Emits an
    explicit `node.lease_expired` event so "the *consequence* of the
    clock is replayable even though the clock is not" (v4 section 3).
    Returns the list of affected node rows (post-transition)."""
    current = now or _now()
    now_s = current.strftime(TS_FORMAT)
    with db_mod.write_txn(conn):
        expired = conn.execute(
            "SELECT * FROM nodes WHERE status = 'in_progress' AND lease_until IS NOT NULL "
            "AND lease_until < ? AND deleted_at IS NULL",
            (now_s,),
        ).fetchall()
        reverted = []
        for node in expired:
            row = nodes_mod._apply_transition(
                conn,
                node,
                to_status="ready",
                lease_actor=node["owner"] or "",
                event_actor_role="daemon",
                event_type="node.lease_expired",
                request_id=None,
                extra_columns={
                    "lease_expiries": node["lease_expiries"] + 1,
                    "owner": None,
                    "lease_until": None,
                },
                actor_evidence="subprocess",
            )
            reverted.append(dict(row))
        return reverted


def reconcile_on_start(
    conn: sqlite3.Connection, *, repo_root: Path | None = None, now: datetime | None = None
) -> dict:
    """Called once when the daemon (re)starts: reconcile expired leases,
    then drain the hook fast-path spool (`.muvue/queue.jsonl`, v4
    section 4a) when `repo_root` is given. This is what makes `muvue
    serve` restartable without losing anything (plan section 1,
    principle 1).

    It deliberately does *not* touch `events.acked_at`: the inbox reads
    "unacked" as "still open", so only a human `ack` may set it. An
    earlier version acked every unacked event here and silently cleared
    the inbox on each restart (see docs/decisions.md)."""
    reverted = reconcile_leases(conn, now=now)
    drained = 0
    if repo_root is not None:
        drained = hooks_mod.drain_queue(conn, Path(repo_root))["drained"]
    return {"reverted_nodes": reverted, "drained_spool": drained}


# --------------------------------------------------------------------------
# Session token (plan v4 section 8a, controls 5/6): minted fresh in
# memory on every `serve` start, never written to disk. Idle sessions
# (no successful `verify_and_touch` call) expire after
# `DEFAULT_IDLE_TIMEOUT_MINUTES` (control 6: "idle sessions expire after
# 8h"; "idle" is read literally as *since the last request*, not since
# issuance -- see docs/decisions.md #85). A brand-new token that has
# never been used is treated as freshly "active" as of the moment
# `SessionManager` is constructed, so a `serve` process that starts and
# is never touched still expires 8h after startup rather than living
# forever.
# --------------------------------------------------------------------------


class SessionManager:
    """One instance per `muvue serve` process. Holds exactly one live
    token (plan section 2: "one human session is meaningful per repo at
    a time"); constructing a new instance (i.e. restarting `serve`)
    mints a fresh 256-bit token and invalidates the previous one purely
    by no longer existing -- there is nothing on disk to invalidate."""

    def __init__(self, *, idle_timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES,
                 now: datetime | None = None) -> None:
        self.token: str = secrets.token_urlsafe(32)  # 256 bits
        self.idle_timeout_minutes = idle_timeout_minutes
        self.last_activity: datetime = now or _now()
        self._nonces: set[str] = set()
        # uvicorn's worker thread and the control-socket thread (`muvue link`)
        # both touch the token and the idle clock.
        self._lock = threading.Lock()

    def _idle(self, current: datetime) -> bool:
        return current - self.last_activity > timedelta(minutes=self.idle_timeout_minutes)

    def mint_nonce(self) -> str:
        """A single-use value for a dashboard link's `#n=` fragment
        (control 5). It is not the token, so a link that leaks through
        browser history or a screenshot is dead once the page has
        exchanged it."""
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._nonces.add(nonce)
        return nonce

    def _check_locked(self, token: str, current: datetime, touch: bool) -> AuthStatus:
        if not secrets.compare_digest(token.encode(), self.token.encode()):
            return "invalid"
        if self._idle(current):
            return "expired"
        if touch:
            self.last_activity = current
        return "ok"

    def check(self, token: str | None, *, now: datetime | None = None, touch: bool = True) -> AuthStatus:
        """Why a token does or doesn't work: `missing` (none sent),
        `invalid` (not this process's token: wrong, or from before a
        restart), `expired` (this process's token, idle past the
        timeout). Only `ok` resets the idle clock, and not even that
        when `touch` is false (background reads authenticate without
        keeping the session alive). Compared as UTF-8 bytes:
        `compare_digest` raises TypeError on non-ASCII `str`."""
        if not token:
            return "missing"
        with self._lock:
            return self._check_locked(token, now or _now(), touch)

    def verify_and_touch(self, token: str | None, *, now: datetime | None = None) -> bool:
        """Constant-time compare against the live token; on success,
        resets the idle clock. Never logged, never written anywhere."""
        return self.check(token, now=now) == "ok"

    def consume_nonce(self, nonce: str | None, *, now: datetime | None = None) -> Literal["ok", "invalid", "expired"]:
        """`ok` once per minted nonce while the session is live;
        `invalid` for an unknown or already-used nonce; `expired` when
        the nonce is real but the session idled out. A real nonce is
        spent either way."""
        if not nonce:
            return "invalid"
        with self._lock:
            if nonce not in self._nonces:
                return "invalid"
            self._nonces.discard(nonce)
            status = self._check_locked(self.token, now or _now(), True)
        return "ok" if status == "ok" else "expired"

    def relink(self, *, now: datetime | None = None) -> tuple[str, bool]:
        """For `muvue link`: a fresh single-use nonce, reviving the
        session if it idled out. An expired session gets a brand-new
        token, so a token that leaked before the expiry stays dead; a
        live one keeps its token, so other signed-in tabs keep working.
        Asking is proof someone is at the server, so the idle clock
        resets. Returns (nonce, renewed)."""
        current = now or _now()
        with self._lock:
            renewed = self._idle(current)
            if renewed:
                self.token = secrets.token_urlsafe(32)
                self._nonces.clear()
            self.last_activity = current
            nonce = secrets.token_urlsafe(32)
            self._nonces.add(nonce)
        return nonce, renewed
