"""P3: real implementations for the agent verbs P0 stubbed
(brief/show/status), needed so the MCP server (core.mcp_server) has
something real to expose."""

from pathlib import Path

import pytest

from muvue.core import asks, db as core_db, gates, nodes, projects, queries
from muvue.core.config import MuvueConfig


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="queries test")


def _ready_task(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto", status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=MuvueConfig())
    return nodes.get_node(conn, task["id"])


def test_show_node_returns_node_notes_commits_touches(conn, project):
    task = _ready_task(conn, project)
    nodes.add_note(conn, task["id"], kind="discovery", text="found it", actor="agent")
    result = queries.show_node(conn, task["id"])
    assert result["node"]["id"] == task["id"]
    assert len(result["notes"]) == 1
    assert result["commits"] == []
    assert result["predicted_touches"] == []


def test_brief_node_surfaces_lessons_and_open_questions(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="a1")
    nodes.fail(conn, task["id"], owner="a1", lesson="don't do X")
    task2 = nodes.get_node(conn, task["id"])
    asks.ask(conn, task["id"], question="which way?", default="A")

    result = queries.brief_node(conn, task["id"])
    assert len(result["lessons"]) == 1
    assert result["open_questions"][0]["text"] == "which way?"


def test_status_summary_counts_by_status(conn, project):
    _ready_task(conn, project)
    second = _ready_task(conn, project)
    nodes.start(conn, second["id"], owner="a1")

    result = queries.status_summary(conn, project["id"])
    assert result["counts"]["ready"] == 1
    assert result["counts"]["in_progress"] == 1


def test_status_summary_without_project_id_covers_everything(conn, project):
    _ready_task(conn, project)
    result = queries.status_summary(conn)
    assert result["project_id"] is None
    assert sum(result["counts"].values()) >= 1
