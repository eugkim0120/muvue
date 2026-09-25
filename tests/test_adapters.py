"""P3: adapter config writers (plan section 7) -- Claude Code's real hook
config, Codex/Gemini/Cursor's best-effort instruction files, doctor's
protocol_version mismatch check, and the Claude Code hook decision logic
(src/muvue/_hook.py, see tests/test_claude_hook_decisions.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muvue.core import adapters, db as core_db, doctor, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


# -- config writers -----------------------------------------------------


def test_install_claude_code_writes_all_four_hooks(tmp_path: Path):
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    hooks = settings["hooks"]
    for event in ("SessionStart", "PreToolUse", "PreCompact", "Stop"):
        assert event in hooks
        commands = [h["command"] for g in hooks[event] for h in g["hooks"]]
        # v4 section 4a (P0.5): the Claude Code adapter's hook commands
        # invoke the stdlib-only fast path -- see src/muvue/_hook.py.
        assert any("-S -m muvue._hook" in c for c in commands)
    assert settings["_muvue"]["protocol_version"] == 1


def test_install_claude_code_preserves_unrelated_settings(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"otherSetting": True, "hooks": {}}))
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    assert settings["otherSetting"] is True


def test_install_claude_code_is_idempotent(tmp_path: Path):
    adapters.install_claude_code(tmp_path, protocol_version=1)
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    for event in ("SessionStart", "PreToolUse", "PreCompact", "Stop"):
        assert len(settings["hooks"][event]) == 1  # not duplicated


def test_install_claude_code_bumps_embedded_protocol_version(tmp_path: Path):
    adapters.install_claude_code(tmp_path, protocol_version=1)
    adapters.install_claude_code(tmp_path, protocol_version=2)
    assert adapters.claude_code_protocol_version(tmp_path) == 2


def test_install_codex_writes_agents_md(tmp_path: Path):
    path = adapters.install_codex(tmp_path, protocol_version=1)
    assert path.name == "AGENTS.md"
    assert "muvue" in path.read_text()
    assert "protocol_version=1" in path.read_text()


def test_install_codex_appends_to_existing_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Existing project notes\n")
    path = adapters.install_codex(tmp_path, protocol_version=1)
    content = path.read_text()
    assert "Existing project notes" in content
    assert "muvue" in content


def test_install_codex_is_idempotent_not_duplicated(tmp_path: Path):
    adapters.install_codex(tmp_path, protocol_version=1)
    adapters.install_codex(tmp_path, protocol_version=1)
    content = (tmp_path / "AGENTS.md").read_text()
    assert content.count("muvue task protocol") <= 1 or content.count("muvue mcp") == 1


def test_install_gemini_writes_gemini_md(tmp_path: Path):
    path = adapters.install_gemini(tmp_path, protocol_version=1)
    assert path.name == "GEMINI.md"


def test_install_cursor_writes_mdc_rule(tmp_path: Path):
    path = adapters.install_cursor(tmp_path, protocol_version=1)
    assert path.suffix == ".mdc"
    assert "protocol_version=1" in path.read_text()


# -- current-node pointer -------------------------------------------------


def test_current_node_round_trips(tmp_path: Path):
    assert adapters.get_current_node(tmp_path) is None
    adapters.set_current_node(tmp_path, 7)
    assert adapters.get_current_node(tmp_path) == 7
    adapters.clear_current_node(tmp_path)
    assert adapters.get_current_node(tmp_path) is None


# -- doctor: protocol_version mismatch -------------------------------------


def test_doctor_warns_on_adapter_protocol_version_mismatch(tmp_path: Path):
    init_repo(tmp_path)
    adapters.install_claude_code(tmp_path, protocol_version=999)  # stale on purpose
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("protocol_version" in issue for issue in report.issues)


def test_doctor_ok_when_adapter_protocol_version_matches(tmp_path: Path):
    init_repo(tmp_path)
    config = MuvueConfig()
    adapters.install_claude_code(tmp_path, protocol_version=config.protocol_version)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True


def test_doctor_ok_when_no_adapter_installed(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
