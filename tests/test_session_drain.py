"""Mission control phase 1: `drain_queue` folds session lines into
`sessions` and `session_touches` in the same transaction as the
`hook.<event>` audit events."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import db as core_db, hooks, nodes, projects
from muvue.core.repo_init import init_repo
from muvue.core.sessions import apply_line

OLD = "2000-01-01T00:00:00.000Z"


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


def _spool(repo: Path, **line) -> None:
    line.setdefault("ts", _hook._now_iso())
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _row(conn, session_id: str):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _line(repo: Path, event: str, session_id: str = "s-1", **extra) -> dict:
    return {"event": event, "node_id": None, "session_id": session_id, "cwd": str(repo), "agent": "claude", **extra}


def test_a_session_start_line_creates_a_hand_session(repo, conn):
    _spool(repo, **_line(repo, "session-start", transcript_path="/t/s-1.jsonl"))
    _drain(conn, repo)
    row = _row(conn, "s-1")
    assert (row["origin"], row["agent"], row["cwd"], row["branch"]) == ("hand", "claude", str(repo), "main")
    assert (row["node_id"], row["project_id"], row["pid"]) == (None, None, None)
    assert row["transcript_path"] == "/t/s-1.jsonl"
    assert row["last_action"] == "session started"
    types = [r["type"] for r in conn.execute("SELECT type FROM events WHERE type LIKE 'hook.%'")]
    assert types == ["hook.session-start"]


def test_pre_tool_use_records_the_touched_path_and_no_audit_event(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "pre-tool-use", tool_name="Edit", file_path=str(repo / "src" / "app.py")))
    _drain(conn, repo)
    assert _row(conn, "s-1")["last_action"] == "Edit src/app.py"
    touches = [r["path"] for r in conn.execute("SELECT path FROM session_touches WHERE session_id = 's-1'")]
    assert touches == ["src/app.py"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'hook.pre-tool-use'").fetchone()[0] == 0


def test_stop_sets_waiting_and_a_prompt_clears_it(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "stop"))
    _drain(conn, repo)
    assert (_row(conn, "s-1")["waiting"], _row(conn, "s-1")["last_action"]) == (1, "turn ended")
    _spool(repo, **_line(repo, "user-prompt-submit"))
    _drain(conn, repo)
    assert (_row(conn, "s-1")["waiting"], _row(conn, "s-1")["last_action"]) == (0, "prompt sent")


def test_session_end_sets_ended_at_and_a_resume_clears_it(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "session-end", reason="logout"))
    _drain(conn, repo)
    assert _row(conn, "s-1")["ended_at"] is not None
    _spool(repo, **_line(repo, "session-start"))
    _drain(conn, repo)
    assert _row(conn, "s-1")["ended_at"] is None


def test_a_session_in_a_node_worktree_is_bound_to_that_node(repo, conn, tmp_path):
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "node-x", str(wt)], cwd=repo, check=True, capture_output=True)
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
    (wt / "sub").mkdir()
    _spool(repo, **{**_line(repo, "session-start", "s-2"), "cwd": str(wt / "sub")})
    _drain(conn, repo)
    row = _row(conn, "s-2")
    assert (row["node_id"], row["project_id"], row["branch"]) == (node["id"], project["id"], "node-x")


def test_the_current_node_file_never_binds_a_session(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    (repo / ".muvue" / "current_node").write_text(str(node["id"]))
    _spool(repo, **{**_line(repo, "session-start", "s-3"), "node_id": node["id"]})
    _drain(conn, repo)
    assert _row(conn, "s-3")["node_id"] is None


def test_lines_from_a_launched_agent_fold_into_the_launched_row(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-1', ?, ?, 'claude', 'launched', ?, ?, ?)",
        (project["id"], node["id"], str(repo), OLD, OLD),
    )
    _spool(repo, **_line(repo, "pre-tool-use", "claude-own", tool_name="Write", file_path=str(repo / "a.py"),
                         launched_session="run-1"))
    _drain(conn, repo)
    assert _row(conn, "claude-own")["alias_of"] == "run-1"
    run = _row(conn, "run-1")
    assert run["last_action"] == "Write a.py"
    assert run["last_event_at"] > OLD
    assert [tuple(r) for r in conn.execute("SELECT session_id, path FROM session_touches")] == [("run-1", "a.py")]
    # A later line with no launched_session still lands on the run.
    _spool(repo, **_line(repo, "stop", "claude-own"))
    _drain(conn, repo)
    assert _row(conn, "run-1")["waiting"] == 1


def test_an_unknown_launched_session_leaves_a_plain_hand_session(repo, conn):
    _spool(repo, **_line(repo, "session-start", "s-4", launched_session="run-missing"))
    _drain(conn, repo)
    row = _row(conn, "s-4")
    assert (row["origin"], row["alias_of"]) == ("hand", None)


def test_a_queue_line_cannot_set_pid_or_origin(repo, conn):
    _spool(repo, **_line(repo, "session-start", "s-5", origin="launched", pid=1))
    _drain(conn, repo)
    row = _row(conn, "s-5")
    assert (row["origin"], row["pid"]) == ("hand", None)


def test_a_path_outside_any_git_worktree_is_not_recorded(repo, conn, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _spool(repo, **_line(repo, "pre-tool-use", tool_name="Edit", file_path=str(outside / "x.py")))
    _drain(conn, repo)
    assert _row(conn, "s-1")["last_action"] == "Edit"
    assert conn.execute("SELECT COUNT(*) FROM session_touches").fetchone()[0] == 0


MALFORMED = [
    {"event": "session-start", "session_id": "has space", "cwd": "/x", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "relative/dir", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": 7},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": "claude", "ts": "yesterday"},
    {"event": "post-merge", "session_id": "s-6", "cwd": "/x", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/a\u0000b", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/a/\ud800", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": "claude", "transcript_path": "/t/\ud800"},
    {"event": "pre-tool-use", "session_id": "s-6", "cwd": "/x", "agent": "claude", "tool_name": "Edit",
     "file_path": "/x/\ud800"},
    {"event": "pre-tool-use", "session_id": "s-6", "cwd": "/x", "agent": "claude", "tool_name": "Edit",
     "file_path": "/x/a\u0000b"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": "claude", "bind_node_id": 2**63},
]


@pytest.mark.parametrize("line", MALFORMED)
def test_malformed_session_lines_make_no_session(repo, conn, line):
    _spool(repo, **line)
    _drain(conn, repo)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert apply_line(conn, {"ts": _hook._now_iso(), **line}) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("line", MALFORMED)
def test_a_bad_line_does_not_poison_the_queue_for_the_good_line_after_it(repo, conn, line):
    _spool(repo, **line)
    _spool(repo, **_line(repo, "session-start", "s-good"))
    _drain(conn, repo)
    assert [r["id"] for r in conn.execute("SELECT id FROM sessions")] == ["s-good"]
    assert not (repo / ".muvue" / "queue.draining").exists()
    queue = repo / ".muvue" / "queue.jsonl"
    assert not queue.exists() or not queue.read_text().strip()
