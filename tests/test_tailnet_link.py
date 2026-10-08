"""`muvue link` names the permanent URL when tailnet identity applies."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from muvue.cli.main import app
from muvue.core import control
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo


def _serve_link(tmp_path: Path, monkeypatch, *, base_url: str, logins: list[str]):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    if logins:
        cfg = repo / ".muvue" / "config.toml"
        cfg.write_text(cfg.read_text().replace("tailnet_logins = []", f"tailnet_logins = {logins!r}".replace("'", '"')))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    sock = control.socket_path(repo)
    srv = control.ControlServer(sock, SessionManager(), base_url=base_url)
    srv.start()
    try:
        return CliRunner().invoke(app, ["link", str(repo)])
    finally:
        srv.stop()


def test_link_prints_permanent_url_for_tailnet_bind(tmp_path, monkeypatch):
    r = _serve_link(tmp_path, monkeypatch, base_url="http://100.64.0.5:8766", logins=["me@example.com"])
    assert r.exit_code == 0, r.output
    assert "dashboard (permanent, signed in by Tailscale identity me@example.com): http://100.64.0.5:8766/" in r.output
    assert "dashboard (one-time link, works once): http://100.64.0.5:8766/#n=" in r.output


def test_link_omits_permanent_url_when_allowlist_empty(tmp_path, monkeypatch):
    r = _serve_link(tmp_path, monkeypatch, base_url="http://100.64.0.5:8766", logins=[])
    assert r.exit_code == 0, r.output
    assert "permanent" not in r.output


def test_link_omits_permanent_url_for_loopback_bind(tmp_path, monkeypatch):
    r = _serve_link(tmp_path, monkeypatch, base_url="http://127.0.0.1:8766", logins=["me@example.com"])
    assert r.exit_code == 0, r.output
    assert "permanent" not in r.output
