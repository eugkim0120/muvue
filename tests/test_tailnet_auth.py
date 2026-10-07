"""Spec A: an allowlisted tailnet peer is signed in without a nonce, cookie
or token. Everything else keeps the existing token flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core.config import ChecksConfig, DaemonConfig, MuvueConfig
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo
from muvue.core.tailnet import TailnetError, TailnetResolver

ME = "me@example.com"
PEER_IP = "100.64.0.9"


def _whois_for(login=ME, tags=None):
    return lambda ip: {"Node": {"User": 1, "Tags": tags}, "UserProfile": {"ID": 1, "LoginName": login}}


def _client(tmp_path: Path, *, logins, transport, client_ip=PEER_IP, session=None) -> TestClient:
    init_repo(tmp_path)
    config = MuvueConfig(checks=ChecksConfig(test="true", lint="true"), daemon=DaemonConfig(tailnet_logins=logins))
    app = create_app(
        tmp_path, config=config, session=session or SessionManager(), tailnet=TailnetResolver(transport)
    )
    return TestClient(app, base_url="http://127.0.0.1", client=(client_ip, 50000))


def test_allowlisted_peer_is_signed_in_without_credentials(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for())
    assert c.get("/auth/check").status_code == 200


def test_login_match_is_case_insensitive(tmp_path):
    c = _client(tmp_path, logins=["ME@Example.com"], transport=_whois_for())
    assert c.get("/auth/check").status_code == 200


def test_whoami_reports_login(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for())
    assert c.get("/auth/whoami").json() == {"login": ME}


def test_other_tailnet_login_stays_signed_out(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for("friend@example.com"))
    r = c.get("/auth/check")
    assert r.status_code == 403 and r.headers["x-muvue-auth"] == "missing"
    assert c.get("/auth/whoami").json() == {"login": None}


def test_tagged_node_is_denied(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for(tags=["tag:ci"]))
    assert c.get("/auth/check").status_code == 403


def test_empty_allowlist_leaves_behaviour_unchanged(tmp_path):
    def transport(ip):
        raise AssertionError("whois must not run when the feature is off")

    c = _client(tmp_path, logins=[], transport=transport)
    assert c.get("/auth/check").status_code == 403


def test_loopback_client_is_never_tailnet_authed(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for(), client_ip="127.0.0.1")
    assert c.get("/auth/check").status_code == 403


def test_x_forwarded_for_is_ignored(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for(), client_ip="127.0.0.1")
    r = c.get("/auth/check", headers={"X-Forwarded-For": PEER_IP, "X-Real-IP": PEER_IP, "Forwarded": f"for={PEER_IP}"})
    assert r.status_code == 403


def test_whois_failure_falls_back_to_token_flow(tmp_path):
    def transport(ip):
        raise TailnetError("socket missing")

    session = SessionManager()
    c = _client(tmp_path, logins=[ME], transport=transport, session=session)
    assert c.get("/auth/check").status_code == 403
    assert c.get("/auth/check", headers={"Authorization": f"Bearer {session.token}"}).status_code == 200


def test_tailnet_peer_is_not_subject_to_idle_expiry(tmp_path):
    from datetime import datetime, timedelta, timezone

    session = SessionManager()
    c = _client(tmp_path, logins=[ME], transport=_whois_for(), session=session)
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=9)
    c.cookies.set("muvue_session", session.token)
    assert c.get("/auth/check").status_code == 200


def test_cross_origin_post_from_allowlisted_peer_is_refused(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for())
    r = c.post("/projects/p1/pause", headers={"Origin": "http://evil.example", "Content-Type": "application/json"})
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid Origin header"


def test_form_post_from_allowlisted_peer_is_refused(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for())
    r = c.post("/projects/p1/pause", data={"a": "b"})
    assert r.status_code == 403


def test_same_origin_json_post_from_allowlisted_peer_is_authed(tmp_path):
    c = _client(tmp_path, logins=[ME], transport=_whois_for())
    r = c.post("/projects/p1/pause", headers={"Content-Type": "application/json"})
    assert r.status_code != 403
