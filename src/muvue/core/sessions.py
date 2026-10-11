"""Agent sessions (mission control phase 1, docs/superpowers/specs/
2026-10-10-mission-control-design.md): one row per run of an agent CLI,
started by muvue's runner (`launched`) or by a person (`hand`).

Rows have two writers. The runner inserts and ends its own `launched`
rows. Everything else arrives as `.muvue/queue.jsonl` lines -- the
Claude Code hooks (`muvue._hook`) and the MCP session tools -- and is
folded in by `apply_line`, inside the drain's transaction. The queue is
untrusted input: a line can create or update only a `hand` row, never
sets `pid` (the one field `POST /sessions/{id}/stop` signals), and is
validated field by field.

A session's node is decided here, not in the hook: a `cwd` inside a
node's worktree, else the runner's node for a launched agent (the
agent's own session id becomes an alias of the launched row, so one
run is one session), else an explicit `bind_node_id` from the MCP
`session_start` tool, else none (off-plan). `.muvue/current_node` is
never used: it is one file per repository.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from muvue import _hook

from . import db as db_mod
from . import gitutil
from .config import SessionsConfig

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LAUNCHED_SESSION_ENV = _hook.LAUNCHED_SESSION_ENV
SESSION_EVENTS = (
    "session-start", "user-prompt-submit", "pre-tool-use", "stop", "pre-compact", "session-end", "heartbeat",
)
# Session lines that are not also recorded as `hook.<event>` audit
# events: PreToolUse fires once per tool call and an MCP heartbeat as
# often as an agent likes, so auditing them would flood `events`.
UNAUDITED_EVENTS = ("pre-tool-use", "heartbeat")
_ACTIONS = {
    "session-start": "session started",
    "user-prompt-submit": "prompt sent",
    "stop": "turn ended",
    "pre-compact": "compacting",
    "session-end": "session ended",
}
_MAX_TOUCHED_PER_LINE = 100
_MIN_INT64, _MAX_INT64 = -(2**63), 2**63 - 1


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _valid_id(value) -> bool:
    return isinstance(value, str) and SESSION_ID_RE.match(value) is not None


def _clean_text(value) -> bool:
    """True unless `value` is a string SQLite or the filesystem layer
    would choke on: a lone surrogate (valid JSON, not encodable as UTF-8)
    or a NUL byte."""
    if not isinstance(value, str):
        return True
    if "\0" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _well_formed(event: dict) -> bool:
    """Boundary check for the free-text and id fields of a queue line.
    Rejecting the whole line (rather than one field) keeps a garbled line
    from half-updating a session, and stops it raising out of the drain,
    which would leave `queue.draining` in place and replay it forever."""
    texts = [event.get(k) for k in ("cwd", "agent", "transcript_path", "tool_name", "file_path", "action")]
    touched = event.get("touched_paths")
    if isinstance(touched, list):
        texts.extend(touched[:_MAX_TOUCHED_PER_LINE])
    if not all(_clean_text(t) for t in texts):
        return False
    bind = event.get("bind_node_id")
    return bind is None or (type(bind) is int and _MIN_INT64 <= bind <= _MAX_INT64)


def _canonical_id(conn: sqlite3.Connection, session_id: str, launched) -> str:
    """The row a line updates. A launched agent's lines name the runner's
    session (`launched_session`, from `MUVUE_SESSION`); the agent's own
    id is recorded once as an alias of it."""
    if _valid_id(launched) and launched != session_id:
        found = db_mod.query_one(
            conn, "SELECT id FROM sessions WHERE id = ? AND origin = 'launched' AND alias_of IS NULL", (launched,),
        )
        if found is not None:
            with db_mod.write_txn(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, project_id, node_id, agent, origin, cwd, "
                    "started_at, last_event_at, alias_of) SELECT ?, project_id, node_id, agent, "
                    "'launched', cwd, started_at, last_event_at, id FROM sessions WHERE id = ?",
                    (session_id, launched),
                )
            return launched
    row = db_mod.query_one(conn, "SELECT alias_of FROM sessions WHERE id = ?", (session_id,))
    if row is not None and row["alias_of"] is not None:
        return row["alias_of"]
    return session_id


def _node_for_cwd(conn: sqlite3.Connection, cwd: str) -> sqlite3.Row | None:
    """The live node whose worktree contains `cwd` (deepest wins)."""
    best = None
    for row in db_mod.query_all(
        conn, "SELECT id, project_id, worktree FROM nodes WHERE worktree IS NOT NULL AND deleted_at IS NULL",
    ):
        top = os.path.realpath(row["worktree"])
        if (cwd == top or cwd.startswith(top + os.sep)) and (best is None or len(top) > len(best[0])):
            best = (top, row)
    return best[1] if best else None


def _explicit_node(conn: sqlite3.Connection, node_id) -> sqlite3.Row | None:
    if type(node_id) is not int:
        return None
    return db_mod.query_one(
        conn, "SELECT id, project_id FROM nodes WHERE id = ? AND deleted_at IS NULL", (node_id,),
    )


def _insert_hand(conn: sqlite3.Connection, session_id: str, *, agent: str, cwd: str, ts: str, event: dict) -> None:
    node = _node_for_cwd(conn, cwd) or _explicit_node(conn, event.get("bind_node_id"))
    transcript = event.get("transcript_path")
    with db_mod.write_txn(conn):
        conn.execute(
            "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, branch, transcript_path, "
            "started_at, last_event_at) VALUES (?, ?, ?, ?, 'hand', ?, ?, ?, ?, ?)",
            (
                session_id, node["project_id"] if node else None, node["id"] if node else None, agent[:64],
                cwd, gitutil.branch_at(cwd), transcript if isinstance(transcript, str) else None, ts, ts,
            ),
        )


def _move(conn: sqlite3.Connection, row: sqlite3.Row, cwd: str) -> None:
    """The session's directory changed: a hand session follows it into a
    node's worktree; a launched session keeps the runner's node."""
    node_id, project_id = row["node_id"], row["project_id"]
    if row["origin"] == "hand":
        node = _node_for_cwd(conn, cwd)
        if node is not None:
            node_id, project_id = node["id"], node["project_id"]
    with db_mod.write_txn(conn):
        conn.execute(
            "UPDATE sessions SET cwd = ?, branch = ?, node_id = ?, project_id = ? WHERE id = ?",
            (cwd, gitutil.branch_at(cwd), node_id, project_id, row["id"]),
        )


def _raw_paths(event: dict) -> list[str]:
    if event.get("event") == "pre-tool-use":
        path = event.get("file_path")
        return [path] if isinstance(path, str) and path else []
    if event.get("event") == "heartbeat":
        paths = event.get("touched_paths")
        if isinstance(paths, list):
            return [p for p in paths[:_MAX_TOUCHED_PER_LINE] if isinstance(p, str) and p]
    return []


def _repo_relative(path: str, cwd: str) -> str | None:
    """`path` relative to the git worktree that contains it; None outside
    any worktree."""
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    top = gitutil.worktree_top(os.path.dirname(full))
    if top is None:
        return None
    rel = os.path.relpath(full, top)
    return None if rel == "." or rel.startswith("..") else rel


def _action(event: dict, paths: list[str]) -> str | None:
    etype = event.get("event")
    if etype == "pre-tool-use":
        tool = event.get("tool_name")
        tool = tool if isinstance(tool, str) and tool else "tool"
        return f"{tool} {paths[0]}" if paths else tool
    if etype == "heartbeat":
        action = event.get("action")
        return action.strip()[:200] if isinstance(action, str) and action.strip() else None
    return _ACTIONS.get(etype)


def apply_line(conn: sqlite3.Connection, event: dict) -> str | None:
    """Fold one queue line into `sessions`/`session_touches`. Returns the
    session id it updated, or None when the line names no valid session
    (most queue lines: commits, pushes, timeouts). Runs in its own
    `write_txn`, which nests into the drain's when there is one."""
    session_id, ts = event.get("session_id"), event.get("ts")
    if event.get("event") not in SESSION_EVENTS or not _valid_id(session_id):
        return None
    if not isinstance(ts, str) or parse_ts(ts) is None:
        return None
    if not _well_formed(event):
        return None
    raw_cwd = event.get("cwd")
    cwd = os.path.realpath(raw_cwd) if isinstance(raw_cwd, str) and os.path.isabs(raw_cwd) else None
    with db_mod.write_txn(conn):
        canonical = _canonical_id(conn, session_id, event.get("launched_session"))
        row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (canonical,))
        if row is None:
            agent = event.get("agent")
            if cwd is None or not isinstance(agent, str) or not agent.strip():
                return None
            _insert_hand(conn, canonical, agent=agent.strip(), cwd=cwd, ts=ts, event=event)
            row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (canonical,))
        elif cwd is not None and cwd != row["cwd"]:
            _move(conn, row, cwd)
        paths = [p for p in (_repo_relative(raw, cwd or row["cwd"]) for raw in _raw_paths(event)) if p]
        etype = event["event"]
        conn.execute(
            "UPDATE sessions SET last_event_at = MAX(last_event_at, ?), "
            "last_action = COALESCE(?, last_action), waiting = ?, "
            "ended_at = CASE WHEN ? = 'session-end' THEN ? WHEN ? = 'session-start' THEN NULL "
            "ELSE ended_at END WHERE id = ?",
            (ts, _action(event, paths), int(etype == "stop"), etype, ts, etype, canonical),
        )
        for path in paths:
            conn.execute(
                "INSERT INTO session_touches (session_id, path, at) VALUES (?, ?, ?) "
                "ON CONFLICT (session_id, path) DO UPDATE SET at = excluded.at",
                (canonical, path, ts),
            )
    return canonical


STATES = ("working", "waiting", "quiet", "idle", "ended")


def session_state(row, now: datetime, cfg: SessionsConfig) -> str:
    """Liveness, computed on read. Ended (ended_at set, or silent past
    `ended_s`) wins, then waiting on you (`waiting` after Stop), then
    working (an event within `working_s`), then idle (silent past
    `idle_s`). Between `working_s` and `idle_s` a session that is not
    waiting is "quiet": it may be in a long tool call, or gone."""
    if row["ended_at"] is not None:
        return "ended"
    last = parse_ts(row["last_event_at"])
    age = (now - last).total_seconds() if last is not None else float("inf")
    if age > cfg.ended_s:
        return "ended"
    if row["waiting"]:
        return "waiting"
    if age <= cfg.working_s:
        return "working"
    if age > cfg.idle_s:
        return "idle"
    return "quiet"


def _launched_spend(conn: sqlite3.Connection, node_id: int, agent: str) -> dict:
    """`node_usage` has no session column, so a launched session shows
    what its agent has used on its node, across every run of that node."""
    row = db_mod.query_one(
        conn,
        "SELECT COALESCE(SUM(in_tokens), 0) AS i, COALESCE(SUM(out_tokens), 0) AS o, "
        "COALESCE(SUM(cost), 0) AS c FROM node_usage WHERE node_id = ? AND agent = ?",
        (node_id, agent),
    )
    return {"in_tokens": row["i"], "out_tokens": row["o"], "cost": row["c"]}


def _view(conn: sqlite3.Connection, row: sqlite3.Row, state: str) -> dict:
    view = {key: row[key] for key in row.keys() if key != "node_worktree"}
    view["waiting"] = bool(row["waiting"])
    view["state"] = state
    view["worktree"] = row["node_worktree"] or gitutil.worktree_top(row["cwd"])
    view["touches"] = [
        r["path"] for r in db_mod.query_all(
            conn, "SELECT path FROM session_touches WHERE session_id = ? ORDER BY at DESC, path", (row["id"],),
        )
    ]
    view["spend"] = (
        _launched_spend(conn, row["node_id"], row["agent"])
        if row["origin"] == "launched" and row["node_id"] is not None else None
    )
    return view


def list_sessions(
    conn: sqlite3.Connection,
    cfg: SessionsConfig,
    *,
    project_id: int | None = None,
    include_ended: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Every session, newest event first, with its computed state. Alias
    rows (a launched agent's own id) are folded into their run. With
    `project_id`, off-plan sessions (no project) are kept: they belong to
    the repository, not to one project."""
    now = now or datetime.now(timezone.utc)
    sql = (
        "SELECT s.*, n.title AS node_title, n.status AS node_status, n.worktree AS node_worktree "
        "FROM sessions s LEFT JOIN nodes n ON n.id = s.node_id WHERE s.alias_of IS NULL"
    )
    params: list = []
    if project_id is not None:
        sql += " AND (s.project_id = ? OR s.project_id IS NULL)"
        params.append(project_id)
    out = []
    for row in db_mod.query_all(conn, sql + " ORDER BY s.last_event_at DESC, s.id", params):
        state = session_state(row, now, cfg)
        if state == "ended" and not include_ended:
            continue
        out.append(_view(conn, row, state))
    return out


def format_ts(dt: datetime) -> str:
    """Millisecond timestamps in the shape `muvue._hook._now_iso` writes,
    so stored values compare correctly as text."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _now_ts() -> str:
    return format_ts(datetime.now(timezone.utc))


def start_launched(conn: sqlite3.Connection, *, node_id: int, project_id: int, agent: str, cwd: str) -> str:
    """Insert the `launched` row for one runner invocation, before the
    driver starts. Returns its id, `run-<uuid>`, which the runner passes
    to the agent as `MUVUE_SESSION`."""
    session_id = f"run-{uuid.uuid4()}"
    now = _now_ts()
    real_cwd = os.path.realpath(cwd)
    with db_mod.write_txn(conn):
        conn.execute(
            "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, branch, started_at, "
            "last_event_at, last_action) VALUES (?, ?, ?, ?, 'launched', ?, ?, ?, ?, 'launched')",
            (session_id, project_id, node_id, agent, real_cwd, gitutil.branch_at(real_cwd), now, now),
        )
    return session_id


def set_pid(conn: sqlite3.Connection, session_id: str, pid: int) -> None:
    """The driver's process (and process group) id, the one thing `stop`
    signals. Only the runner calls this; no queue line can."""
    with db_mod.write_txn(conn):
        conn.execute("UPDATE sessions SET pid = ? WHERE id = ? AND origin = 'launched'", (pid, session_id))


def end_launched(conn: sqlite3.Connection, session_id: str) -> None:
    now = _now_ts()
    with db_mod.write_txn(conn):
        conn.execute(
            "UPDATE sessions SET ended_at = ?, waiting = 0, last_event_at = MAX(last_event_at, ?), "
            "last_action = 'run ended' WHERE id = ? AND ended_at IS NULL",
            (now, now, session_id),
        )


class SessionError(Exception):
    """A session request muvue refuses; the message says why."""


def _with_launched(line: dict) -> dict:
    launched = os.environ.get(LAUNCHED_SESSION_ENV)
    if launched:
        line["launched_session"] = launched
    return line


def _checked_text(name: str, value) -> str:
    """The same text rule `apply_line` enforces (`_clean_text`), raised
    here so the tool fails instead of queueing a line the drain drops."""
    if not isinstance(value, str):
        raise SessionError(f"{name} must be a string")
    if not _clean_text(value):
        raise SessionError(f"{name} contains a NUL byte or a lone surrogate")
    return value


def _checked_id(session_id) -> str:
    if not _valid_id(session_id):
        raise SessionError(f"not a session id: {session_id!r}")
    return session_id


def spool_start(repo_root: Path, *, agent: str, cwd: str, node_id: int | None = None) -> dict:
    """MCP `session_start`, for agent CLIs without Claude Code hooks: the
    same queue line a SessionStart hook writes. Returns the new session's
    id (`mcp-<uuid>`), which later heartbeat and end calls name. An MCP
    server started by a launched agent inherits `MUVUE_SESSION`, so its
    session folds into the run like a hook's would."""
    if not _checked_text("agent", agent).strip():
        raise SessionError("agent must be a non-empty string")
    if not os.path.isabs(_checked_text("cwd", cwd)):
        raise SessionError(f"cwd must be an absolute path, got {cwd!r}")
    if node_id is not None and not (type(node_id) is int and _MIN_INT64 <= node_id <= _MAX_INT64):
        raise SessionError(f"node_id must be a 64-bit integer, got {node_id!r}")
    session_id = f"mcp-{uuid.uuid4()}"
    line = {"event": "session-start", "ts": _hook._now_iso(), "session_id": session_id, "agent": agent.strip(), "cwd": cwd}
    if node_id is not None:
        line["bind_node_id"] = node_id
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}


def spool_heartbeat(
    repo_root: Path, session_id: str, *, action: str | None = None, touched_paths: list[str] | None = None,
) -> dict:
    """MCP `session_heartbeat`: the session is working; `action` becomes
    its last action and `touched_paths` (absolute, or relative to its
    directory) its touched files."""
    line = {"event": "heartbeat", "ts": _hook._now_iso(), "session_id": _checked_id(session_id)}
    if action is not None:
        line["action"] = _checked_text("action", action)
    if touched_paths is not None:
        if not isinstance(touched_paths, list) or not all(isinstance(p, str) for p in touched_paths):
            raise SessionError("touched_paths must be a list of strings")
        if len(touched_paths) > _MAX_TOUCHED_PER_LINE:
            raise SessionError(f"touched_paths holds at most {_MAX_TOUCHED_PER_LINE} paths, got {len(touched_paths)}")
        for path in touched_paths:
            _checked_text("touched_paths entry", path)
        line["touched_paths"] = touched_paths
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}


def spool_end(repo_root: Path, session_id: str) -> dict:
    """MCP `session_end`."""
    line = {"event": "session-end", "ts": _hook._now_iso(), "session_id": _checked_id(session_id)}
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}
