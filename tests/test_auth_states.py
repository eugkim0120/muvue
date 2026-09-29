"""Sign-in plan Task 1: every auth refusal says why (X-Muvue-Auth), an
idle-expired session is 401 not 403, and a pasted token can be turned
into the HttpOnly session cookie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def session() -> SessionManager:
    return SessionManager()


@pytest.fixture
def client(tmp_path: Path, session: SessionManager) -> TestClient:
    init_repo(tmp_path)
    app = create_app(tmp_path, config=MuvueConfig(checks=ChecksConfig(test="true", lint="true")), session=session)
    return TestClient(app, base_url=BASE_URL)


def _expire(session: SessionManager) -> None:
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=9)


def test_no_token_is_403_missing(client):
    r = client.get("/auth/check")
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "missing"
    assert r.json()["detail"] == "missing session token"


def test_wrong_token_is_403_invalid_and_names_muvue_link(client):
    r = client.get("/auth/check", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "invalid"
    assert "muvue link" in r.json()["detail"]


def test_non_ascii_token_is_403_invalid_not_500(client):
    r = client.get("/auth/check", headers={"Authorization": "Bearer tok\u00e9n".encode("utf-8")})
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "invalid"


def test_expired_session_is_401_expired_and_says_how_to_recover(client, session):
    _expire(session)
    r = client.get("/auth/check", headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"
    assert r.json()["detail"] == "session expired after 8 hours without activity; run `muvue link` on the server for a fresh link"


def test_expired_session_refuses_mutations_with_401(client, session):
    _expire(session)
    r = client.post("/projects", json={"goal": "x"}, headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"


def test_exchange_with_a_real_nonce_after_expiry_is_401_expired(client, session):
    nonce = session.mint_nonce()
    _expire(session)
    r = client.post("/auth/exchange", json={"nonce": nonce})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"
    assert "muvue_session" not in client.cookies


def test_exchange_with_an_unknown_nonce_is_still_403(client):
    r = client.post("/auth/exchange", json={"nonce": "never-minted"})
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid or already used nonce"
    assert r.headers["x-muvue-auth"] == "invalid"


def test_auth_session_turns_a_bearer_token_into_the_cookie(client, session):
    r = client.post("/auth/session", json={}, headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.cookies.get("muvue_session") == session.token
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/auth/check").status_code == 200  # the cookie alone now works


def test_auth_session_without_a_token_is_403_and_sets_no_cookie(client):
    r = client.post("/auth/session", json={})
    assert r.status_code == 403
    assert "muvue_session" not in client.cookies
