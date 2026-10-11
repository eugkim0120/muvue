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
from datetime import datetime, timezone

from muvue import _hook

from . import db as db_mod
from . import gitutil

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


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _valid_id(value) -> bool:
    return isinstance(value, str) and SESSION_ID_RE.match(value) is not None


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
