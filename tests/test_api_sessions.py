"""Mission control phase 1: the session and worktree read views, and
Stop for sessions muvue launched (never for hand-started ones)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import git_init_with_commit
from muvue import _hook
from muvue.api import create_app
from muvue.core import db as core_db, hooks, nodes, projects, sessions
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def app(repo):
    return create_app(repo, config=MuvueConfig(checks=ChecksConfig(test="true", lint="true")))


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def auth(app) -> dict:
    return {"Authorization": f"Bearer {app.state.session.token}"}


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _hand_session(conn, repo: Path, session_id: str = "s-1", *events: str) -> None:
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        for event in ("session-start", *events):
            f.write(json.dumps({"event": event, "ts": _hook._now_iso(), "node_id": None, "session_id": session_id,
                                "cwd": str(repo), "agent": "claude"}) + "\n")
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _launched(conn, repo: Path) -> str:
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    return sessions.start_launched(conn, node_id=node["id"], project_id=project["id"], agent="claude", cwd=str(repo))


def test_the_views_need_a_session(client):
    assert client.get("/sessions").status_code == 403
    assert client.get("/worktrees").status_code == 403
    assert client.post("/sessions/s-1/stop", json={}).status_code == 403


def test_get_sessions_lists_live_sessions_with_their_state(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    _hand_session(conn, repo, "s-2", "session-end")
    body = client.get("/sessions", headers=auth).json()
    assert [(s["id"], s["state"], s["origin"]) for s in body["sessions"]] == [("s-1", "working", "hand")]
    every = client.get("/sessions?include_ended=1", headers=auth).json()
    assert {s["id"] for s in every["sessions"]} == {"s-1", "s-2"}


def test_get_worktrees_lists_the_checkout(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    body = client.get("/worktrees", headers=auth).json()
    assert body["errors"] == []
    assert [(w["branch"], w["is_base"], w["session_ids"]) for w in body["worktrees"]] == [("main", True, ["s-1"])]


def test_stop_refuses_a_hand_session(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    response = client.post("/sessions/s-1/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "started by hand; stop it where it runs"


def test_stop_of_an_unknown_session_is_404(client, auth):
    assert client.post("/sessions/nope/stop", json={}, headers=auth).status_code == 404


def test_stop_must_be_json(client, auth):
    response = client.post(
        "/sessions/s-1/stop", content="a=b",
        headers={**auth, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 403


def test_stop_signals_a_launched_sessions_process_group(client, auth, conn, repo):
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = _launched(conn, repo)
        sessions.set_pid(conn, run_id, proc.pid)
        response = client.post(f"/sessions/{run_id}/stop", json={}, headers=auth)
        assert response.status_code == 200
        assert response.json() == {"session_id": run_id, "signalled": proc.pid}
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'session.stopped'").fetchone()[0] == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_stop_reaches_the_run_through_the_agents_own_id(client, auth, conn, repo):
    run_id = _launched(conn, repo)
    conn.execute(
        "INSERT INTO sessions (id, agent, origin, cwd, started_at, last_event_at, alias_of) "
        "VALUES ('claude-own', 'claude', 'launched', ?, 't', 't', ?)", (str(repo), run_id),
    )
    response = client.post("/sessions/claude-own/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert "has no process yet" in response.json()["detail"]


def test_stop_of_an_ended_run_is_refused(client, auth, conn, repo):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, 999999)
    sessions.end_launched(conn, run_id)
    response = client.post(f"/sessions/{run_id}/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert "has already ended" in response.json()["detail"]


def test_stop_of_a_process_gone_before_the_signal_marks_the_run_ended(conn, repo, monkeypatch):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, 4242)
    monkeypatch.setattr(sessions, "_is_group_leader", lambda pid: True)
    calls = []

    def gone(pid, sig):
        calls.append(pid)
        raise ProcessLookupError(pid)

    with pytest.raises(sessions.SessionError, match="is no longer running; marked it ended"):
        sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty", kill=gone)
    assert calls == [4242]
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] is not None


def test_stop_of_a_pid_that_leads_no_group_is_never_signalled(conn, repo):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, 4242)

    def must_not_be_called(pid, sig):
        raise AssertionError("signalled a pid that leads no group")

    with pytest.raises(sessions.SessionError, match="does not lead a process group; marked it ended"):
        sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty", kill=must_not_be_called)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] is not None


@pytest.mark.parametrize("own_pid", [os.getpid, os.getpgrp])
def test_stop_never_signals_muvues_own_process_or_group(conn, repo, own_pid):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, own_pid())

    def must_not_be_called(pid, sig):
        raise AssertionError("signalled muvue itself")

    with pytest.raises(sessions.SessionError, match="muvue's own process"):
        sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty", kill=must_not_be_called)


def test_stop_refuses_a_bad_origin_and_a_bad_host(client, auth):
    bad_origin = client.post("/sessions/s-1/stop", json={}, headers={**auth, "Origin": "http://evil.example.com"})
    assert bad_origin.status_code == 403
    bad_host = client.post("/sessions/s-1/stop", json={}, headers={**auth, "Host": "evil.example.com"})
    assert bad_host.status_code == 403


def test_stop_never_signals_a_pid_that_is_not_a_process_group_leader(conn, repo):
    """A recycled pid belongs to some other process; the driver always
    leads its own group, so a non-leader is never ours to signal."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        run_id = _launched(conn, repo)
        sessions.set_pid(conn, run_id, proc.pid)
        with pytest.raises(sessions.SessionError, match="does not lead a process group"):
            sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty")
        assert proc.poll() is None
        assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] is not None
    finally:
        proc.kill()
        proc.wait()


def test_stop_reports_a_process_it_may_not_signal(conn, repo):
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = _launched(conn, repo)
        sessions.set_pid(conn, run_id, proc.pid)

        def denied(pid, sig):
            raise PermissionError(pid)

        with pytest.raises(sessions.SessionError, match="not permitted"):
            sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty", kill=denied)
    finally:
        proc.kill()
        proc.wait()
