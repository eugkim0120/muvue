"""v4 section 8a, W9 of the delta closure: JSON content-type on every
mutating request (body or not), a one-time fragment nonce that is not
the session token, the header-token fallback for embedded dashboards,
and doctor's bind probe."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import db as core_db, doctor, projects
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def app(repo):
    return create_app(repo, config=MuvueConfig(checks=ChecksConfig(test="true", lint="true")))


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def bearer(app) -> dict:
    return {"Authorization": f"Bearer {app.state.session.token}"}


@pytest.fixture
def project_id(repo) -> int:
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        return projects.create_project(conn, goal="w9")["id"]
    finally:
        conn.close()


def test_bodyless_mutation_without_json_content_type_is_rejected(client, bearer, project_id):
    r = client.post(f"/projects/{project_id}/pause", headers=bearer)
    assert r.status_code == 403
    assert "application/json" in r.json()["detail"]


def test_bodyless_mutation_with_json_content_type_is_accepted(client, bearer, project_id):
    r = client.post(
        f"/projects/{project_id}/pause", headers={**bearer, "Content-Type": "application/json"},
    )
    assert r.status_code == 200


def test_nonce_is_not_the_session_token():
    session = SessionManager()
    nonce = session.mint_nonce()
    assert nonce != session.token
    assert not session.verify_and_touch(nonce)


def test_nonce_exchange_sets_cookie_once(client, app):
    nonce = app.state.session.mint_nonce()
    r = client.post("/auth/exchange", json={"nonce": nonce})
    assert r.status_code == 200
    assert "muvue_session" in client.cookies
    assert "token" not in r.json()
    client.cookies.clear()
    again = client.post("/auth/exchange", json={"nonce": nonce})
    assert again.status_code == 403
    assert "muvue_session" not in client.cookies


def test_session_token_is_not_accepted_as_a_nonce(client, app):
    r = client.post("/auth/exchange", json={"nonce": app.state.session.token})
    assert r.status_code == 403


def test_embedded_exchange_returns_header_token(client, app):
    """Inside a VS Code webview the dashboard is a cross-site iframe, so
    the SameSite=Strict cookie is never sent. The page asks for the
    token itself and keeps it in memory."""
    nonce = app.state.session.mint_nonce()
    r = client.post("/auth/exchange", json={"nonce": nonce, "header": True})
    assert r.status_code == 200
    assert r.json()["token"] == app.state.session.token


def test_mint_nonce_endpoint_requires_session(client, bearer):
    assert client.post("/auth/nonce", json={}).status_code == 403
    r = client.post("/auth/nonce", json={}, headers=bearer)
    assert r.status_code == 200
    nonce = r.json()["nonce"]
    assert client.post("/auth/exchange", json={"nonce": nonce}).status_code == 200


def test_auth_check(client, bearer):
    assert client.get("/auth/check").status_code == 403
    assert client.get("/auth/check", headers=bearer).status_code == 200


# 127.0.0.2 stands in for "another interface of this machine": it is
# a different address from 127.0.0.1, so a listener bound only to
# 127.0.0.1 refuses it, and the tests never open a public port.
OTHER_ADDRESS = "127.0.0.2"


def test_bind_probe_flags_a_daemon_reachable_beyond_loopback(monkeypatch):
    monkeypatch.setattr(doctor, "non_loopback_addresses", lambda: [OTHER_ADDRESS])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((OTHER_ADDRESS, 0))
        s.listen(1)
        port = s.getsockname()[1]
        issues = doctor.probe_bind(port)
    assert issues and "control 1" in issues[0]


def test_bind_probe_passes_a_loopback_only_daemon(monkeypatch):
    monkeypatch.setattr(doctor, "non_loopback_addresses", lambda: [OTHER_ADDRESS])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert doctor.probe_bind(port) == []


def test_non_loopback_addresses_excludes_loopback():
    assert all(not a.startswith("127.") for a in doctor.non_loopback_addresses())
