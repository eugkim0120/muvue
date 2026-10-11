"""Mission control phase 1: agent CLIs without Claude Code hooks report
sessions through three MCP tools, which spool the same queue lines the
hooks write."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import mcp_server
from muvue.core import db as core_db, hooks, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


@pytest.fixture(autouse=True)
def _no_launched_env(monkeypatch):
    monkeypatch.delenv("MUVUE_SESSION", raising=False)


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


def _call(repo: Path, name: str, arguments: dict) -> dict:
    return mcp_server.handle_request(
        repo, MuvueConfig(),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )


def _result(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def test_tools_list_names_the_session_tools(repo):
    response = mcp_server.handle_request(repo, MuvueConfig(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"session_start", "session_heartbeat", "session_end"} <= names


def test_session_tools_round_trip_through_the_drain(repo, conn):
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo)}))["session_id"]
    assert session_id.startswith("mcp-")
    _call(repo, "session_heartbeat", {"session_id": session_id, "action": "running tests", "touched_paths": ["src/a.py"]})
    _drain(conn, repo)
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert (row["agent"], row["origin"], row["last_action"], row["ended_at"]) == ("codex", "hand", "running tests", None)
    assert [r["path"] for r in conn.execute("SELECT path FROM session_touches")] == ["src/a.py"]
    _call(repo, "session_end", {"session_id": session_id})
    _drain(conn, repo)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()["ended_at"] is not None


def test_session_start_binds_an_explicit_node(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo), "node_id": node["id"]}))["session_id"]
    _drain(conn, repo)
    row = conn.execute("SELECT node_id, project_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert (row["node_id"], row["project_id"]) == (node["id"], project["id"])


def test_a_launched_agents_mcp_session_folds_into_its_run(repo, conn, monkeypatch):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-9', ?, ?, 'codex', 'launched', ?, '2000-01-01T00:00:00.000Z', '2000-01-01T00:00:00.000Z')",
        (project["id"], node["id"], str(repo)),
    )
    monkeypatch.setenv("MUVUE_SESSION", "run-9")
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo)}))["session_id"]
    _drain(conn, repo)
    assert conn.execute("SELECT alias_of FROM sessions WHERE id = ?", (session_id,)).fetchone()["alias_of"] == "run-9"


@pytest.mark.parametrize("name, arguments, code, text", [
    ("session_start", {"agent": "codex", "cwd": "relative"}, -32000, "absolute"),
    ("session_start", {"agent": "", "cwd": "/x"}, -32000, "agent"),
    ("session_start", {"agent": "codex", "cwd": "/x", "node_id": "7"}, -32000, "node_id"),
    ("session_heartbeat", {"session_id": "has space"}, -32000, "not a session id"),
    ("session_heartbeat", {"session_id": "s", "touched_paths": "a.py"}, -32000, "touched_paths"),
    ("session_heartbeat", {"action": "x"}, -32602, "session_id"),
])
def test_bad_arguments_are_json_rpc_errors(repo, name, arguments, code, text):
    response = _call(repo, name, arguments)
    assert response["error"]["code"] == code
    assert text in response["error"]["message"]
    assert not (repo / ".muvue" / "queue.jsonl").exists()


@pytest.mark.parametrize("name, arguments, text", [
    ("session_start", {"agent": "co\0dex", "cwd": "/x"}, "agent"),
    ("session_start", {"agent": "codex", "cwd": "/x\0y"}, "cwd"),
    ("session_start", {"agent": "codex", "cwd": "/x\ud800"}, "cwd"),
    ("session_start", {"agent": "codex", "cwd": "/x", "node_id": 2**63}, "node_id"),
    ("session_heartbeat", {"session_id": "s", "action": "a\0b"}, "action"),
    ("session_heartbeat", {"session_id": "s", "action": "a\ud800"}, "action"),
    ("session_heartbeat", {"session_id": "s", "touched_paths": ["ok", "a\0b"]}, "touched_paths"),
    ("session_heartbeat", {"session_id": "s", "touched_paths": ["\ud800"]}, "touched_paths"),
    ("session_heartbeat", {"session_id": "s", "touched_paths": [f"f{i}" for i in range(101)]}, "touched_paths"),
])
def test_input_apply_line_would_drop_is_refused_and_not_queued(repo, name, arguments, text):
    response = _call(repo, name, arguments)
    assert response["error"]["code"] == -32000
    assert text in response["error"]["message"]
    assert not (repo / ".muvue" / "queue.jsonl").exists()


def test_a_full_touched_paths_list_is_accepted_by_apply_line(repo, conn):
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo)}))["session_id"]
    _call(repo, "session_heartbeat", {"session_id": session_id, "touched_paths": [f"f{i}" for i in range(100)]})
    _drain(conn, repo)
    assert conn.execute("SELECT COUNT(*) FROM session_touches").fetchone()[0] == 100
