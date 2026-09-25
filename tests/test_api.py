"""P2: FastAPI app mirrors CLI verbs 1:1 (plan section 4), every mutating
endpoint session-token gated (plan v4 section 8a), inbox latency (P2
acceptance #1).

`TestClient` is pointed at `base_url="http://127.0.0.1"` throughout: the
daemon-security `Host` middleware (v4 section 8a control 2) rejects any
other Host value, including httpx's `testserver` default -- see
docs/decisions.md #84/#86. `create_app` is called without an explicit
`port`, so the Host/Origin checks only enforce the loopback *hostname*
here, not an exact port (a real `muvue serve` process always passes
`port`; the exact-port matching is covered against a real running
daemon in tests/test_daemon_security.py).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import asks, db as core_db, gates, nodes, projects
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def app(repo, config):
    return create_app(repo, config=config)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def token(app) -> str:
    return app.state.session.token


@pytest.fixture
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


# -- agent verbs (v4 section 8a: session-gated like every other mutation) --


def test_start_done_via_api(client, ready_task, auth_headers):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "in_progress"

    r = client.post(f"/nodes/{node_id}/done", json={"owner": "agent-1"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "done"
    assert r.json()["auto_approved"] is True


def test_start_without_session_token_is_rejected(client, ready_task):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"})
    assert r.status_code == 403


# -- opt-in `run_checks` wiring at the API layer (P3 decision #38) ---------


def test_done_without_run_checks_ignores_a_failing_test_command(repo):
    config = MuvueConfig(checks=ChecksConfig(test="false", lint="true"))
    app = create_app(repo, config=config)
    client = TestClient(app, base_url=BASE_URL)
    headers = {"Authorization": f"Bearer {app.state.session.token}"}
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

    client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"}, headers=headers)
    r = client.post(f"/nodes/{node_id}/done", json={"owner": "agent-1"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "done"
    assert r.json()["auto_approved"] is True


def test_done_with_run_checks_flags_a_failing_test_command(repo):
    config = MuvueConfig(checks=ChecksConfig(test="false", lint="true"))
    app = create_app(repo, config=config)
    client = TestClient(app, base_url=BASE_URL)
    headers = {"Authorization": f"Bearer {app.state.session.token}"}
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

    client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"}, headers=headers)
    r = client.post(
        f"/nodes/{node_id}/done", json={"owner": "agent-1", "run_checks": True}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["node"]["status"] == "review"
    assert r.json()["auto_approved"] is False


def test_start_with_agent_param_is_recorded_but_does_not_spawn(client, ready_task, conn, auth_headers):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/start?agent=claude", json={"owner": "agent-1"}, headers=auth_headers)
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
    assert r.status_code == 403


def test_approve_with_valid_session_token_succeeds(client, conn, config, auth_headers):
    project = projects.create_project(conn, goal="p2")
    spec = gates.submit_spec(conn, project_id=project["id"], title="spec", body_md="# x")
    r = client.post(
        f"/nodes/{spec['id']}/approve",
        json={"target": "spec"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_approve_with_bad_token_rejected(client, ready_task):
    r = client.post(
        f"/nodes/{ready_task['task']['id']}/reject",
        json={"feedback": "no"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403


def test_pause_refuses_start_then_resume_allows_it(client, conn, ready_task, auth_headers):
    project_id = ready_task["project"]["id"]
    r = client.post(f"/projects/{project_id}/pause", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["phase"] == "paused"

    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"}, headers=auth_headers)
    assert r.status_code == 409

    r = client.post(f"/projects/{project_id}/resume", headers=auth_headers)
    assert r.json()["phase"] == "executing"
    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"}, headers=auth_headers)
    assert r.status_code == 200


# -- `answer` human verb (docs/protocol.md gap: no entry point existed) ----


def test_answer_endpoint_requires_session_token(client, ready_task, conn):
    q = asks.ask(conn, ready_task["task"]["id"], question="q?", default="yes")["question"]
    r = client.post(f"/questions/{q['id']}/answer", json={"text": "no"})
    assert r.status_code == 403


def test_answer_endpoint_with_valid_token_answers_the_question(client, ready_task, conn, auth_headers):
    q = asks.ask(conn, ready_task["task"]["id"], question="q?", default="yes")["question"]
    r = client.post(
        f"/questions/{q['id']}/answer",
        json={"text": "yes, proceed"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["question"]["status"] == "answered"
    assert r.json()["question"]["answer"] == "yes, proceed"


def test_comment_endpoint_adds_feedback_note(client, ready_task, conn, auth_headers):
    node_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{node_id}/comment", json={"text": "looks good"}, headers=auth_headers)
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


# -- auth exchange (v4 section 8a control 5) --------------------------------


def test_exchange_sets_httponly_samesite_strict_cookie(client, token):
    r = client.post("/auth/exchange", json={"token": token})
    assert r.status_code == 200
    cookie_header = r.headers.get("set-cookie", "")
    assert "muvue_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header or "SameSite=Strict" in cookie_header


def test_exchange_with_wrong_token_rejected(client):
    r = client.post("/auth/exchange", json={"token": "wrong"})
    assert r.status_code == 403
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}


def test_cookie_from_exchange_authorizes_mutating_requests(client, token, ready_task):
    r = client.post("/auth/exchange", json={"token": token})
    assert r.status_code == 200
    # No Authorization header this time -- the cookie the exchange just
    # set (and httpx/TestClient's cookie jar now carries) is what
    # authorizes this mutating request.
    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"})
    assert r.status_code == 200


def test_events_without_cursor_returns_the_newest_window(client, conn):
    """Regression: `/events?limit=N` ran `ORDER BY id ASC LIMIT`, so once a
    repo had more than N events the timeline showed the oldest N forever."""
    from muvue.core import events as events_mod

    for i in range(5):
        events_mod.record_event(conn, project_id=None, node_id=None, actor="human",
                                type_="test.tick", payload={"i": i})
    newest = conn.execute("SELECT MAX(id) m FROM events").fetchone()["m"]
    rows = client.get("/events?limit=3").json()
    assert [r["id"] for r in rows] == [newest - 2, newest - 1, newest]


def test_events_with_cursor_pages_forward_oldest_first(client, conn):
    from muvue.core import events as events_mod

    first = events_mod.record_event(conn, project_id=None, node_id=None, actor="human",
                                    type_="test.tick", payload={})
    for _ in range(3):
        events_mod.record_event(conn, project_id=None, node_id=None, actor="human",
                                type_="test.tick", payload={})
    rows = client.get(f"/events?since_id={first}&limit=2").json()
    assert [r["id"] for r in rows] == [first + 1, first + 2]
