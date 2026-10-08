"""`doctor` warns when a tailnet allowlist is configured but tailscaled's
LocalAPI cannot be reached, since every allowlisted peer would silently
fall back to token sign-in."""

from __future__ import annotations

from pathlib import Path

from muvue.core import doctor
from muvue.core.repo_init import init_repo
from muvue.core.tailnet import TailnetError


def _enable(repo: Path) -> None:
    cfg = repo / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace("tailnet_logins = []", 'tailnet_logins = ["me@example.com"]'))


def test_warns_when_allowlist_set_and_localapi_unreachable(tmp_path, monkeypatch):
    init_repo(tmp_path)
    _enable(tmp_path)

    def down():
        raise TailnetError("socket missing")

    monkeypatch.setattr(doctor.tailnet_mod, "localapi_reachable", down)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert any("tailnet" in w and "socket missing" in w for w in report.warnings)


def test_info_when_allowlist_set_and_localapi_reachable(tmp_path, monkeypatch):
    init_repo(tmp_path)
    _enable(tmp_path)
    monkeypatch.setattr(doctor.tailnet_mod, "localapi_reachable", lambda: None)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not any("tailnet" in w for w in report.warnings)
    assert any("tailnet identity" in i and "me@example.com" in i for i in report.info)


def test_silent_when_allowlist_empty(tmp_path, monkeypatch):
    init_repo(tmp_path)

    def boom():
        raise AssertionError("must not probe tailscaled when the feature is off")

    monkeypatch.setattr(doctor.tailnet_mod, "localapi_reachable", boom)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not any("tailnet" in m for m in [*report.warnings, *report.info])
