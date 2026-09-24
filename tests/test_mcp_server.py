"""P3: MCP stdio server exposes agent verbs only (plan section 4: "Never
exposed over MCP" for human verbs). `handle_request` is tested directly
(pure request-in/response-out) rather than through a real stdio
subprocess -- see mcp_server.py's module docstring for why that's a
faithful test of the same code path `run_stdio` calls."""

from pathlib import Path

import pytest

from muvue.core import db as core_db, gates, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo
from muvue.mcp_server import TOOLS, handle_request

HUMAN_VERBS = {
    "approve", "reject", "ack", "merge", "close", "pause", "resume", "handoff",
    "import", "answer",
}


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def ready_task_id(repo_root, config) -> int:
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    try:
        project = projects.create_project(conn, goal="mcp test")
        task = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="t",
            criteria=["passes"], criteria_mode="auto", status="pending",
        )
        gates.approve_gate2(conn, project["id"], config=config)
        return task["id"]
    finally:
        conn.close()


def test_only_agent_verbs_are_exposed_never_human_verbs():
    assert HUMAN_VERBS.isdisjoint(TOOLS)
    for expected in ("brief", "show", "start", "done", "fail", "note", "ask", "wait", "replan", "status"):
        assert expected in TOOLS


def test_initialize_returns_protocol_info(repo_root, config):
    resp = handle_request(repo_root, config, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["serverInfo"]["name"] == "muvue"


def test_tools_list_matches_tools_dict(repo_root, config):
    resp = handle_request(repo_root, config, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == set(TOOLS)


def test_notification_gets_no_response(repo_root, config):
    resp = handle_request(repo_root, config, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


def test_unknown_method_returns_error(repo_root, config):
    resp = handle_request(repo_root, config, {"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert resp["error"]["code"] == -32601


def test_tools_call_start_and_done_round_trip(repo_root, config, ready_task_id):
    start_resp = handle_request(
        repo_root, config,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "start", "arguments": {"node_id": ready_task_id, "owner": "claude-1"}}},
    )
    import json
    assert "error" not in start_resp
    started = json.loads(start_resp["result"]["content"][0]["text"])
    assert started["node"]["status"] == "in_progress"

    done_resp = handle_request(
        repo_root, config,
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "done", "arguments": {"node_id": ready_task_id, "owner": "claude-1"}}},
    )
    done = json.loads(done_resp["result"]["content"][0]["text"])
    assert done["node"]["status"] == "done"


def test_tools_call_unknown_tool_is_an_error(repo_root, config):
    resp = handle_request(
        repo_root, config,
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "approve", "arguments": {}}},
    )
    assert resp["error"]["code"] == -32601


def test_tools_call_missing_argument_is_an_error(repo_root, config, ready_task_id):
    resp = handle_request(
        repo_root, config,
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "start", "arguments": {"node_id": ready_task_id}}},
    )
    assert resp["error"]["code"] == -32602


def test_tools_call_core_error_is_surfaced_not_raised(repo_root, config):
    resp = handle_request(
        repo_root, config,
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
         "params": {"name": "start", "arguments": {"node_id": 999999, "owner": "x"}}},
    )
    assert resp["error"]["code"] == -32000
