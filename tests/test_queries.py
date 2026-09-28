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
    nodes.fail(conn, task["id"], owner="a1", lesson="don't do X", trigger="t", do_instead="d", scope="s")
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


def test_tail_log_includes_breakdown_log(tmp_path):
    from muvue.core import queries

    logs = tmp_path / ".muvue" / "logs"
    logs.mkdir(parents=True)
    (logs / "breakdown-4.log").write_text("planning line\n")
    assert "planning line" in queries.tail_log(tmp_path, 4, 50)


def test_tail_project_log_reads_run_project_log(tmp_path):
    from muvue.core import queries

    logs = tmp_path / ".muvue" / "logs"
    logs.mkdir(parents=True)
    (logs / "run-project-2.log").write_text("a\nb\nc\n")
    assert queries.tail_project_log(tmp_path, 2, 2) == "b\nc\n"
    assert queries.tail_project_log(tmp_path, 3, 2) == ""


def test_project_activity_reports_latest_breakdown_outcome_and_live_runners(tmp_path, monkeypatch):
    from muvue.core import db as core_db, events, nodes, projects, queries, runners
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        p = projects.create_project(conn, goal="g")
        spec = nodes.create_node(conn, project_id=p["id"], kind="spec", title="s", criteria=[], criteria_mode="manual", predicted_touches=[], status="ready")
        for type_, payload in [("breakdown.started", {"agent": "fake"}), ("breakdown.failed", {"reason": "boom"})]:
            events.record_event(conn, project_id=p["id"], node_id=spec["id"], actor="agent", actor_evidence="tty", type_=type_, payload=payload)
        monkeypatch.setattr(runners, "live", lambda root: [
            {"pid": 11, "project_id": p["id"], "started_at": "t", "kind": "breakdown", "node_id": spec["id"]},
            {"pid": 12, "project_id": p["id"] + 1, "started_at": "t"},
            {"pid": 13, "project_id": None, "started_at": "t"},
        ])
        a = queries.project_activity(conn, tmp_path, p["id"])
    finally:
        conn.close()
    assert a["active"] == [
        {"kind": "breakdown", "pid": 11, "node_id": spec["id"], "started_at": "t"},
        {"kind": "run", "pid": 13, "node_id": None, "started_at": "t"},
    ]
    [b] = a["breakdowns"]
    assert (b["node_id"], b["type"], b["agent"], b["reason"], b["created"]) == (spec["id"], "breakdown.failed", "fake", "boom", None)
    assert a["working"] == []


def test_register_records_kind_and_node(tmp_path):
    import json
    from muvue.core import runners

    path = runners.register(tmp_path, 3, kind="breakdown", node_id=9)
    entry = json.loads(path.read_text())
    assert (entry["kind"], entry["node_id"], entry["project_id"]) == ("breakdown", 9, 3)
