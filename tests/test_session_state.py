"""Mission control phase 1: liveness is computed on read from
last_event_at, waiting and ended_at, with the [sessions] thresholds
(120 s working, 900 s idle, 7200 s ended; "quiet" in between working
and idle)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue.core import db as core_db, hooks, nodes, projects, sessions
from muvue.core.config import SessionsConfig
from muvue.core.repo_init import init_repo

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
CFG = SessionsConfig()


def _ts(age_s: float) -> str:
    t = NOW - timedelta(seconds=age_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _state_row(age_s: float, *, waiting: int = 0, ended_at: str | None = None) -> dict:
    return {"last_event_at": _ts(age_s), "waiting": waiting, "ended_at": ended_at}


@pytest.mark.parametrize("row, state", [
    (_state_row(5), "working"),
    (_state_row(120), "working"),
    (_state_row(121), "quiet"),
    (_state_row(900), "quiet"),
    (_state_row(901), "idle"),
    (_state_row(7200), "idle"),
    (_state_row(7201), "ended"),
    (_state_row(500, waiting=1), "waiting"),
    (_state_row(7201, waiting=1), "ended"),
    (_state_row(5, ended_at=_ts(1)), "ended"),
])
def test_session_state(row, state):
    assert sessions.session_state(row, NOW, CFG) == state


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _spool(repo: Path, event: str, session_id: str, age_s: float, **extra) -> None:
    line = {"event": event, "ts": _ts(age_s), "node_id": None, "session_id": session_id,
            "cwd": str(repo), "agent": "claude", **extra}
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _states(conn, **kw) -> dict[str, str]:
    return {s["id"]: s["state"] for s in sessions.list_sessions(conn, CFG, now=NOW, **kw)}


def test_drained_lines_move_a_session_through_its_states(repo, conn):
    _spool(repo, "session-start", "s-1", 60)
    _spool(repo, "pre-tool-use", "s-1", 30, tool_name="Bash")
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "working"}
    _spool(repo, "stop", "s-1", 20)
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "waiting"}
    _spool(repo, "user-prompt-submit", "s-1", 10)
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "working"}
    _spool(repo, "session-end", "s-1", 5)
    _drain(conn, repo)
    assert _states(conn) == {}
    assert _states(conn, include_ended=True) == {"s-1": "ended"}


def test_quiet_and_idle_come_from_the_clock_alone(repo, conn):
    _spool(repo, "session-start", "quiet-one", 300)
    _spool(repo, "session-start", "idle-one", 1000)
    _drain(conn, repo)
    assert _states(conn) == {"quiet-one": "quiet", "idle-one": "idle"}


def test_the_list_is_newest_first_and_carries_touches(repo, conn):
    _spool(repo, "session-start", "older", 50)
    _spool(repo, "session-start", "newer", 40)
    _spool(repo, "pre-tool-use", "newer", 30, tool_name="Edit", file_path=str(repo / "a.py"))
    _spool(repo, "pre-tool-use", "newer", 20, tool_name="Write", file_path=str(repo / "b.py"))
    _drain(conn, repo)
    listed = sessions.list_sessions(conn, CFG, now=NOW)
    assert [s["id"] for s in listed] == ["newer", "older"]
    newer = listed[0]
    assert newer["touches"] == ["b.py", "a.py"]
    assert newer["waiting"] is False
    assert newer["worktree"] == str(repo)
    assert newer["spend"] is None


def test_project_filter_keeps_off_plan_sessions(repo, conn, tmp_path):
    mine = projects.create_project(conn, goal="mine")
    other = projects.create_project(conn, goal="other")
    node = nodes.create_node(conn, project_id=other["id"], kind="task", title="t", status="ready")
    wt = tmp_path / "wt"
    wt.mkdir()
    conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
    _spool(repo, "session-start", "off-plan", 10)
    _spool(repo, "session-start", "theirs", 10, cwd=str(wt))
    _drain(conn, repo)
    assert set(_states(conn, project_id=mine["id"])) == {"off-plan"}
    assert set(_states(conn, project_id=other["id"])) == {"off-plan", "theirs"}


def test_a_launched_session_shows_its_nodes_spend_and_hides_its_alias(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="Detect pitch", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-1', ?, ?, 'claude', 'launched', ?, ?, ?)",
        (project["id"], node["id"], str(repo), _ts(30), _ts(30)),
    )
    conn.execute(
        "INSERT INTO node_usage (node_id, agent, in_tokens, out_tokens, requests, cost) VALUES (?, 'claude', 1000, 200, 1, 0.12)",
        (node["id"],),
    )
    _spool(repo, "pre-tool-use", "claude-own", 10, tool_name="Bash", launched_session="run-1")
    _drain(conn, repo)
    listed = sessions.list_sessions(conn, CFG, now=NOW)
    assert [s["id"] for s in listed] == ["run-1"]
    run = listed[0]
    assert (run["node_title"], run["node_status"], run["state"]) == ("Detect pitch", "ready", "working")
    assert run["spend"] == {"in_tokens": 1000, "out_tokens": 200, "cost": pytest.approx(0.12)}


def test_alias_rows_are_never_listed(repo, conn):
    _spool(repo, "session-start", "solo", 10)
    _drain(conn, repo)
    conn.execute(
        "INSERT INTO sessions (id, agent, origin, cwd, started_at, last_event_at, alias_of) "
        "VALUES ('alias-1', 'claude', 'launched', ?, ?, ?, 'solo')",
        (str(repo), _ts(1), _ts(1)),
    )
    assert _states(conn) == {"solo": "working"}
    assert _states(conn, include_ended=True) == {"solo": "working"}
