"""P2: FastAPI app mirrors CLI verbs 1:1 (plan section 4), human verbs
gated by session token (plan section 8), inbox latency (P2 acceptance #1).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import asks, daemon, db as core_db, gates, nodes, projects
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def client(repo, config) -> TestClient:
    app = create_app(repo, config=config)
    return TestClient(app)


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def ready_task(conn, config):
    project = projects.create_project(conn, goal="api test")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto",
        predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return {"project": project, "task": nodes.get_node(conn, task["id"])}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_dashboard_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_openapi_documents_the_api(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "/nodes/{node_id}/start" in spec["paths"]
    assert "/nodes/{node_id}/approve" in spec["paths"]


# -- acceptance #1: inbox latency -------------------------------------------


def test_inbox_endpoint_responds_under_one_second(client, ready_task):
    started = time.monotonic()
    r = client.get("/inbox")
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert elapsed < 1.0, f"inbox took {elapsed:.3f}s"


# -- agent verbs --------------------------------------------------------


def test_start_done_via_api(client, ready_task):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"})
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "in_progress"

    r = client.post(f"/nodes/{node_id}/done", json={"owner": "agent-1"})
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "done"
    assert r.json()["auto_approved"] is True


# -- opt-in `run_checks` wiring at the API layer (P3 decision #38) ---------


def test_done_without_run_checks_ignores_a_failing_test_command(repo):
    config = MuvueConfig(checks=ChecksConfig(test="false", lint="true"))
    app = create_app(repo, config=config)
    client = TestClient(app)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="api run_checks test")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto",
        predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    node_id = task["id"]
    conn.close()

    client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"})
    r = client.post(f"/nodes/{node_id}/done", json={"owner": "agent-1"})
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "done"
    assert r.json()["auto_approved"] is True


def test_done_with_run_checks_flags_a_failing_test_command(repo):
    config = MuvueConfig(checks=ChecksConfig(test="false", lint="true"))
    app = create_app(repo, config=config)
    client = TestClient(app)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="api run_checks test")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto",
        predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    node_id = task["id"]
    conn.close()

    client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"})
    r = client.post(
        f"/nodes/{node_id}/done", json={"owner": "agent-1", "run_checks": True}
    )
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "review"
    assert r.json()["auto_approved"] is False


def test_start_with_agent_param_is_recorded_but_does_not_spawn(client, ready_task, conn):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/start?agent=claude", json={"owner": "agent-1"})
    assert r.status_code == 200
    events = conn.execute(
        "SELECT * FROM events WHERE node_id = ? AND type = 'node.agent_requested'",
        (node_id,),
    ).fetchall()
    assert len(events) == 1
    assert '"agent": "claude"' in events[0]["payload"]


def test_show_node_includes_notes_and_commits(client, ready_task):
    node_id = ready_task["task"]["id"]
    r = client.get(f"/nodes/{node_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["id"] == node_id
    assert "notes" in body and "commits" in body and "predicted_touches" in body


def test_node_diff_stub_endpoint(client, ready_task):
    node_id = ready_task["task"]["id"]
    r = client.get(f"/nodes/{node_id}/diff")
    assert r.status_code == 200
    assert r.json()["node_id"] == node_id


def test_node_logs_stream_endpoint(client, ready_task):
    node_id = ready_task["task"]["id"]
    r = client.get(f"/nodes/{node_id}/logs")
    assert r.status_code == 200
    lines = [l for l in r.text.splitlines() if l.strip()]
    assert len(lines) > 0


def test_show_node_404_for_missing_node(client):
    r = client.get("/nodes/99999")
    assert r.status_code == 404


# -- human verbs are session-token gated ---------------------------------


def test_approve_requires_session_token(client, ready_task):
    r = client.post(f"/nodes/{ready_task['task']['id']}/reject", json={"feedback": "no"})
    assert r.status_code == 401


def test_approve_with_valid_session_token_succeeds(client, repo, conn, config):
    project = projects.create_project(conn, goal="p2")
    spec = gates.submit_spec(conn, project_id=project["id"], title="spec", body_md="# x")
    token = daemon.create_session(repo)
    r = client.post(
        f"/nodes/{spec['id']}/approve",
        json={"target": "spec"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_approve_with_bad_token_rejected(client, repo, ready_task):
    daemon.create_session(repo)
    r = client.post(
        f"/nodes/{ready_task['task']['id']}/reject",
        json={"feedback": "no"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_pause_refuses_start_then_resume_allows_it(client, repo, conn, ready_task):
    token = daemon.create_session(repo)
    project_id = ready_task["project"]["id"]
    r = client.post(
        f"/projects/{project_id}/pause", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["phase"] == "paused"

    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"})
    assert r.status_code == 409

    r = client.post(
        f"/projects/{project_id}/resume", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json()["phase"] == "executing"
    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"})
    assert r.status_code == 200


# -- `answer` human verb (docs/protocol.md gap: no entry point existed) ----


def test_answer_endpoint_requires_session_token(client, ready_task, conn):
    q = asks.ask(conn, ready_task["task"]["id"], question="q?", default="yes")["question"]
    r = client.post(f"/questions/{q['id']}/answer", json={"text": "no"})
    assert r.status_code == 401


def test_answer_endpoint_with_valid_token_answers_the_question(client, repo, ready_task, conn):
    q = asks.ask(conn, ready_task["task"]["id"], question="q?", default="yes")["question"]
    token = daemon.create_session(repo)
    r = client.post(
        f"/questions/{q['id']}/answer",
        json={"text": "yes, proceed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["question"]["status"] == "answered"
    assert r.json()["question"]["answer"] == "yes, proceed"


def test_comment_endpoint_adds_feedback_note(client, ready_task, conn):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/comment", json={"text": "looks good"})
    assert r.status_code == 200
    notes = conn.execute(
        "SELECT * FROM notes WHERE node_id = ? AND kind = 'feedback'", (node_id,)
    ).fetchall()
    assert any(n["text"] == "looks good" for n in notes)


def test_kpis_endpoint_present_with_stubbed_and_real_fields(client):
    r = client.get("/kpis")
    assert r.status_code == 200
    body = r.json()
    assert body["drift_pct"] == 0.0
    assert "rubber_stamp_rate" in body
