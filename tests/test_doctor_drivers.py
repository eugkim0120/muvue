"""v4 section 6/7 doctor deltas: driver auth and pinned version, adapter
protocol_version across every installed adapter (a warning), and
`--repair` listing orphan worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue.core import adapters, db as core_db, doctor, nodes, projects, strict
from muvue.core.repo_init import init_repo


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    init_repo(root)
    return root


def _add_agent(repo: Path, block: str) -> None:
    config = repo / ".muvue" / "config.toml"
    config.write_text(config.read_text() + "\n" + block)


def _run(repo: Path, **kw) -> doctor.DoctorReport:
    return doctor.run_doctor(repo, skip_security_probes=True, **kw)


def test_version_satisfies():
    assert doctor.version_satisfies("2.1.281", ">=2.0") is True
    assert doctor.version_satisfies("2.1.281", ">=2.0,<2.1") is False
    assert doctor.version_satisfies("0.9", "==0.9.0") is True
    assert doctor.version_satisfies("no version here", ">=1") is None


def test_driver_version_within_pin_is_reported(repo):
    _add_agent(repo, '[agents.vendor]\ncommand = "true"\ncost_model = "usd"\nauth_check = "echo vendor-cli 2.1.3"\n'
                     'pinned_version = ">=2.0"\n')
    report = _run(repo)
    assert any("agents.vendor: 2.1.3" in line for line in report.info)
    assert not [w for w in report.warnings if "agents.vendor" in w]


def test_driver_outside_pin_warns_with_providers_link(repo):
    _add_agent(repo, '[agents.vendor]\ncommand = "true"\ncost_model = "usd"\nauth_check = "echo vendor-cli 1.4.0"\n'
                     'pinned_version = ">=2.0"\n')
    warnings = [w for w in _run(repo).warnings if "agents.vendor" in w]
    assert len(warnings) == 1
    assert "pinned_version" in warnings[0] and "docs/providers.md" in warnings[0]


def test_failing_auth_check_warns_as_logged_out(repo):
    _add_agent(repo, '[agents.vendor]\ncommand = "true"\ncost_model = "usd"\nauth_check = "echo session expired >&2; exit 1"\n')
    warnings = [w for w in _run(repo).warnings if "agents.vendor" in w]
    assert len(warnings) == 1
    assert "session expired" in warnings[0] and "docs/providers.md" in warnings[0]


def test_every_adapter_protocol_version_is_checked_and_warns(repo):
    adapters.install_codex(repo, protocol_version=0)
    adapters.install_cursor(repo, protocol_version=0)
    report = _run(repo)
    stale = [w for w in report.warnings if "protocol_version" in w]
    assert any("AGENTS.md" in w for w in stale)
    assert any("muvue.mdc" in w for w in stale)
    assert report.ok  # a warning, not an error (plan section 7)


def test_repair_lists_orphan_worktrees(repo):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="g")
    live = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.close()
    root = strict.worktrees_root(repo)
    (root / "node-9999").mkdir(parents=True)
    (root / f"node-{live['id']}").mkdir()
    listed = [line for line in _run(repo, repair=True).info if line.startswith("orphan worktree")]
    assert listed == [f"orphan worktree: {root / 'node-9999'} (no such node)"]
    assert (root / "node-9999").exists()  # listed, never deleted
    assert not [line for line in _run(repo).info if line.startswith("orphan worktree")]
