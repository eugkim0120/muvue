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
from muvue.core import asks, db as core_db, events, gates, nodes, projects
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    # `done` runs [checks] itself (v4 section 5); an empty temp repo has
    # no test suite, so use commands that pass.
    return MuvueConfig(checks=ChecksConfig(test="true", lint="true"))


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


# -- `done` runs auto checks by default (v4 section 5) -------------------


def test_done_runs_checks_by_default_and_flags_a_failure(repo):
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
    assert r.json()["node"]["status"] == "review"
    assert r.json()["auto_approved"] is False


def test_start_with_agent_spawns_a_tracked_runner_that_finishes_the_node(repo):
    """`POST /nodes/{id}/start?agent=X` (plan section 4) launches `muvue
    run --node ID --agent X`; the fake agent completes the node."""
    import time as _time

    from conftest import use_passing_checks
    from muvue.core import load_config

    use_passing_checks(repo)
    config = load_config(repo / ".muvue" / "config.toml")
    app = create_app(repo, config=config)
    client = TestClient(app, base_url=BASE_URL)
    headers = {"Authorization": f"Bearer {app.state.session.token}"}
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(c, goal="spawn")
    task = nodes.create_node(
        c, project_id=project["id"], kind="task", title="t", criteria=["ok"],
        criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(c, project["id"], config=config)

    r = client.post(f"/nodes/{task['id']}/start?agent=fake", json={}, headers=headers)
    assert r.status_code == 200, r.text
    spawned = r.json()["spawned"]
    assert spawned["agent"] == "fake" and spawned["pid"] > 0
    deadline = _time.monotonic() + 30
    while _time.monotonic() < deadline:
        if nodes.get_node(c, task["id"])["status"] == "done":
            break
        _time.sleep(0.2)
    assert nodes.get_node(c, task["id"])["status"] == "done", (repo / spawned["log"]).read_text()
    assert nodes.get_node(c, task["id"])["owner"] == "runner:fake"
    c.close()

    again = client.post(f"/nodes/{task['id']}/start?agent=fake", json={}, headers=headers)
    assert again.status_code == 409
    unknown = client.post(f"/nodes/{task['id']}/start?agent=nope", json={}, headers=headers)
    assert unknown.status_code == 422


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
    r = client.post(f"/projects/{project_id}/pause", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["project"]["phase"] == "paused"

    r = client.post(f"/nodes/{ready_task['task']['id']}/start", json={"owner": "agent-1"}, headers=auth_headers)
    assert r.status_code == 409

    r = client.post(f"/projects/{project_id}/resume", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.json()["project"]["phase"] == "executing"
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


def test_exchange_sets_httponly_samesite_strict_cookie(client, app):
    r = client.post("/auth/exchange", json={"nonce": app.state.session.mint_nonce()})
    assert r.status_code == 200
    cookie_header = r.headers.get("set-cookie", "")
    assert "muvue_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header or "SameSite=Strict" in cookie_header


def test_exchange_with_wrong_nonce_rejected(client):
    r = client.post("/auth/exchange", json={"nonce": "wrong"})
    assert r.status_code == 403
    assert "set-cookie" not in {k.lower() for k in r.headers.keys()}


def test_cookie_from_exchange_authorizes_mutating_requests(client, app, ready_task):
    r = client.post("/auth/exchange", json={"nonce": app.state.session.mint_nonce()})
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


def test_create_project_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "voxscore: voice to sheet music"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["goal"] == "voxscore: voice to sheet music"
    assert body["project"]["phase"] == "planning"
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (body["project"]["id"],)).fetchone()
    assert row is not None


def test_create_spec_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(
        f"/projects/{project_id}/spec",
        json={"title": "Voxscore", "body_md": "Record voice.\nDetect pitch."},
        headers=auth_headers,
    )
    assert r.status_code == 200
    node = r.json()["node"] if "node" in r.json() else r.json()
    assert node["kind"] == "spec"
    assert node["title"] == "Voxscore"
    assert node["status"] == "pending"


def test_add_task_under_pending_spec(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "S", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{spec_id}/children", json={"title": "Record voice", "body_md": "capture mic"}, headers=auth_headers)
    assert r.status_code == 200
    child = r.json()["node"]
    assert child["kind"] == "task"
    assert child["parent_id"] == spec_id


def test_add_task_with_carries_dependency(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "S", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "Record voice"}, headers=auth_headers)
    upstream_id = r.json()["node"]["id"]

    r = client.post(
        f"/nodes/{spec_id}/children",
        json={"title": "Detect pitch", "depends_on": [{"id": upstream_id, "carries": "audio frames"}]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    downstream_id = r.json()["node"]["id"]
    row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?", (downstream_id, upstream_id)
    ).fetchone()
    assert row["carries"] == "audio frames"


def test_add_subtask_under_approved_task_uses_replan(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/children", json={"title": "sub 1", "predicted_touches": ["a.py"]}, headers=auth_headers)
    assert r.status_code == 200
    child = r.json()["node"]
    assert child["kind"] == "subtask"
    assert child["parent_id"] == task_id


def test_add_subtask_under_unapproved_task_uses_create_node(client, conn, auth_headers):
    """Task 5's parked Case C, covered now that Important #3 extracted the
    shared `core.revisions.add_child` helper: a subtask under a task that
    has NOT yet been Gate-2 approved is created directly (`create_node`),
    same as a task under a spec, not routed through `replan_add_subtask`
    (which would refuse it with a GateError)."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]
    assert conn.execute("SELECT criteria_hash FROM nodes WHERE id = ?", (task_id,)).fetchone()["criteria_hash"] is None

    r = client.post(f"/nodes/{task_id}/children", json={"title": "sub 1"}, headers=auth_headers)

    assert r.status_code == 200, r.text
    child = r.json()["node"]
    assert child["kind"] == "subtask"
    assert child["parent_id"] == task_id
    assert child["status"] == "pending"


def test_add_child_under_subtask_kind_is_refused(client, ready_task, auth_headers):
    """Task 5's parked Case D: an invalid parent kind (a subtask, which
    can never itself have children) is refused, not silently accepted."""
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/children", json={"title": "sub 1", "predicted_touches": ["a.py"]}, headers=auth_headers)
    subtask_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{subtask_id}/children", json={"title": "grandchild"}, headers=auth_headers)

    assert r.status_code == 409
    assert "subtask" in r.json()["detail"]


def test_add_child_under_removed_spec_is_refused(client, conn, auth_headers):
    """Important #2 (second half): a soft-deleted node stays fully
    actionable via `get_node`, which doesn't filter `deleted_at` -- adding
    a child under an already-removed spec must not silently create a live
    orphan under a deleted parent."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/remove", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 200

    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)

    assert r.status_code == 409
    assert conn.execute("SELECT COUNT(*) c FROM nodes WHERE parent_id = ?", (spec_id,)).fetchone()["c"] == 0


def test_edit_removed_node_is_refused(client, auth_headers):
    """Important #2 (second half): a removed node stays fully actionable
    via `get_node`, which doesn't filter `deleted_at` -- `/edit` on an
    already-removed node must refuse, not silently rewrite a deleted
    node's title/body."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/remove", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 200

    r = client.post(f"/nodes/{spec_id}/edit", json={"title": "renamed"}, headers=auth_headers)

    assert r.status_code == 409


def test_breakdown_removed_node_is_refused(client, conn, auth_headers):
    """Important #2 (second half): `/breakdown` on an already-removed node
    must refuse before ever spawning a subprocess against it."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    conn.execute("UPDATE nodes SET status = 'ready' WHERE id = ?", (spec_id,))
    conn.commit()
    r = client.post(f"/nodes/{spec_id}/remove", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 200

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=auth_headers)

    assert r.status_code == 409


def test_create_project_requires_auth(client):
    r = client.post("/projects", json={"goal": "g"})
    assert r.status_code == 403


def test_create_project_requires_json(client, auth_headers):
    # SecurityMiddleware's control 4 (JSON-only) rejects non-JSON
    # Content-Type for every mutating route before the handler runs --
    # see test_daemon_security.py::test_form_encoded_post_403s_before_any_side_effect
    # for the same 403 against an existing route.
    r = client.post("/projects", data="goal=g", headers={**auth_headers, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code in (400, 403, 415, 422)


def test_remove_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]

    # /remove is a body-less POST, so it needs the explicit
    # Content-Type: application/json header the same as /pause (control 4,
    # see test_pause_refuses_start_then_resume_allows_it above).
    r = client.post(f"/nodes/{task_id}/remove", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert conn.execute("SELECT deleted_at FROM nodes WHERE id = ?", (task_id,)).fetchone()["deleted_at"] is not None


def test_remove_endpoint_refuses_after_gate2(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/remove", headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code == 409


def test_edit_endpoint_before_gate2(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{task_id}/edit", json={"title": "renamed"}, headers=auth_headers)
    assert r.status_code == 200
    assert conn.execute("SELECT title FROM nodes WHERE id = ?", (task_id,)).fetchone()["title"] == "renamed"


def test_edit_endpoint_refuses_after_gate2(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/edit", json={"title": "x"}, headers=auth_headers)
    assert r.status_code == 409


def test_agents_lists_configured_names_without_a_session(repo):
    from muvue.core.config import AgentConfig

    config = MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"zed": AgentConfig(command="zed", cost_model="usd"), "claude": AgentConfig(command="claude", cost_model="usd")},
    )
    c = TestClient(create_app(repo, config=config), base_url=BASE_URL)
    r = c.get("/agents")
    assert r.status_code == 200
    assert r.json() == {"agents": ["claude", "zed"]}


# -- Task 9: POST /nodes/{id}/breakdown and POST /projects/{id}/run --------


# -- Task 10: GET /agents/status and GET /nodes/{id}/runs -----------------


def test_agent_status_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.get(f"/agents/status?project_id={project_id}", headers=auth_headers)
    assert r.status_code == 200
    assert "agents" in r.json()
    # The `config` fixture has no `[agents.*]` entries, so the only agent
    # that shows up is the one `RoutingConfig`'s defaults route every
    # role to: "claude".
    assert any(a["agent"] == "claude" for a in r.json()["agents"])


def test_node_runs_endpoint(client, ready_task, auth_headers):
    node_id = ready_task["task"]["id"]
    r = client.get(f"/nodes/{node_id}/runs", headers=auth_headers)
    assert r.status_code == 200
    assert "runs" in r.json()


def test_node_runs_endpoint_404s_for_missing_node(client, auth_headers):
    r = client.get("/nodes/999999/runs", headers=auth_headers)
    assert r.status_code == 404


def test_node_runs_endpoint_lists_start_and_done_events(client, ready_task, auth_headers):
    node_id = ready_task["task"]["id"]
    client.post(f"/nodes/{node_id}/start", json={"owner": "agent-1"}, headers=auth_headers)
    client.post(f"/nodes/{node_id}/done", json={"owner": "agent-1"}, headers=auth_headers)
    r = client.get(f"/nodes/{node_id}/runs", headers=auth_headers)
    assert r.status_code == 200
    types = [run["type"] for run in r.json()["runs"]]
    assert types == ["node.start", "node.done"]


def test_node_runs_endpoint_includes_breakdown_events(client, conn, ready_task, auth_headers):
    """Important #7: the design spec wants breakdowns shown in a node's
    Runs section too, not just node.start/done/fail."""
    node_id = ready_task["task"]["id"]
    events.record_event(
        conn, project_id=ready_task["project"]["id"], node_id=node_id, actor="agent",
        actor_evidence="tty", type_="breakdown.started", payload={"agent": "claude"},
    )
    events.record_event(
        conn, project_id=ready_task["project"]["id"], node_id=node_id, actor="agent",
        actor_evidence="tty", type_="breakdown.finished", payload={"created": []},
    )
    r = client.get(f"/nodes/{node_id}/runs", headers=auth_headers)
    assert r.status_code == 200
    types = [run["type"] for run in r.json()["runs"]]
    assert "breakdown.started" in types
    assert "breakdown.finished" in types


def _fake_agent_client(repo):
    """A client backed by `repo`'s own `config.toml` (routes spec/task to
    the `fake` agent -- `DEFAULT_CONFIG_TOML`), instead of the module-level
    `config` fixture, which declares no `[agents.*]` at all. Needed for
    `/breakdown` since Important #4's fix makes the agent check strict."""
    from muvue.core import load_config

    config = load_config(repo / ".muvue" / "config.toml")
    app = create_app(repo, config=config)
    return TestClient(app, base_url=BASE_URL), {"Authorization": f"Bearer {app.state.session.token}"}


def test_breakdown_endpoint_spawns_process_and_records_finished(repo, conn, monkeypatch):
    """Important #4 regression: with the agent check now strict, a properly
    configured agent must actually run to completion and record a real
    `breakdown.finished` event -- not just return a pid (a bare pid was the
    exact shape of "success" that hid the old bug: an empty `config.agents`
    let the endpoint return 200 while the subprocess exited immediately
    with nothing ever recorded)."""
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    client, headers = _fake_agent_client(repo)
    r = client.post("/projects", json={"goal": "g"}, headers=headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=headers)
    spec_id = r.json()["node"]["id"]
    conn.execute("UPDATE nodes SET status = 'ready' WHERE id = ?", (spec_id,))
    conn.commit()

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=headers)
    assert r.status_code == 200, r.text
    spawned = r.json()["spawned"]
    assert "pid" in spawned

    deadline = time.monotonic() + 30
    types: list[str] = []
    while time.monotonic() < deadline:
        types = [
            r["type"] for r in conn.execute(
                "SELECT type FROM events WHERE node_id = ? AND type LIKE 'breakdown.%'", (spec_id,)
            ).fetchall()
        ]
        if "breakdown.finished" in types:
            break
        time.sleep(0.2)
    assert "breakdown.finished" in types, (repo / spawned["log"]).read_text()


def test_breakdown_endpoint_422_for_unconfigured_agent(client, conn, auth_headers):
    """Important #4: an empty/misconfigured `config.agents` must refuse
    up front (422), not silently spawn a subprocess doomed to exit with
    nothing ever recorded."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    conn.execute("UPDATE nodes SET status = 'ready' WHERE id = ?", (spec_id,))
    conn.commit()

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=auth_headers)
    assert r.status_code == 422


def test_breakdown_endpoint_409_if_already_running(repo, conn, monkeypatch):
    import muvue.core.runners as runners_mod

    client, headers = _fake_agent_client(repo)
    r = client.post("/projects", json={"goal": "g"}, headers=headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=headers)
    spec_id = r.json()["node"]["id"]
    monkeypatch.setattr(runners_mod, "live", lambda repo_root: [{"pid": 1, "project_id": project_id, "started_at": "now"}])

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=headers)
    assert r.status_code == 409


def test_run_endpoint_spawns_process_when_a_task_is_ready(client, ready_task, auth_headers):
    r = client.post(f"/projects/{ready_task['project']['id']}/run", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "pid" in r.json()["spawned"]


def test_run_endpoint_409s_with_a_reason_in_planning(client, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/run", json={}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "Nothing to run yet: approve the task list first."


def test_graph_endpoint_includes_carries_and_agent(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "A"}, headers=auth_headers)
    a_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "B", "depends_on": [{"id": a_id, "carries": "audio"}]}, headers=auth_headers)

    r = client.get(f"/graph?project_id={project_id}", headers=auth_headers)
    body = r.json()
    node_a = next(n for n in body["nodes"] if n["id"] == a_id)
    assert "agent" in node_a
    edge = next(e for e in body["edges"] if e["from"] == a_id)
    assert edge["carries"] == "audio"


def test_graph_endpoint_strips_runner_prefix_from_agent_field(client, conn, auth_headers):
    """Important #6: `/graph`'s `agent` field used the raw `owner` value
    directly, showing "runner:claude" for a node the runner leased
    (`owner=f"runner:{agent}"`) instead of the plain agent name
    `agent_status` shows for the same work -- the two endpoints must
    agree."""
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]
    conn.execute("UPDATE nodes SET owner = 'runner:claude', status = 'in_progress' WHERE id = ?", (task_id,))
    conn.commit()

    r = client.get(f"/graph?project_id={project_id}", headers=auth_headers)
    node = next(n for n in r.json()["nodes"] if n["id"] == task_id)
    assert node["agent"] == "claude"


def test_project_activity_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.get(f"/projects/{project_id}/activity", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"active", "breakdowns", "working"}
    assert client.get("/projects/9999/activity", headers=auth_headers).status_code == 404
    assert client.get(f"/projects/{project_id}/activity").status_code == 403


def test_project_logs_endpoint(client, repo, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    (repo / ".muvue" / "logs").mkdir(parents=True, exist_ok=True)
    (repo / ".muvue" / "logs" / f"run-project-{project_id}.log").write_text("runner said hi\n")
    r = client.get(f"/projects/{project_id}/logs", headers=auth_headers)
    assert r.status_code == 200
    assert "runner said hi" in r.text
