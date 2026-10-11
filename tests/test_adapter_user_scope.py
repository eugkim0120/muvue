"""Mission control phase 1: the Claude Code adapter installs six hooks,
`--user` installs them machine-wide in ~/.claude/settings.json as
record-only hooks, `doctor` says which install is active, and a user
install survives `init`/`uninit` of a repository."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import adapters, doctor, repo_init
from muvue.core.config import PROTOCOL_VERSION

SIX = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PreCompact", "Stop", "SessionEnd"]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("MUVUE_SESSION", "MUVUE_HOOK_SCOPE", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


def _commands(settings: dict, event: str) -> list[str]:
    return [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]


def test_the_repo_install_writes_six_hooks(tmp_path, home):
    path = adapters.install_claude_code(tmp_path, protocol_version=PROTOCOL_VERSION)
    settings = json.loads(path.read_text())
    assert list(settings["hooks"]) == SIX
    for event in SIX:
        assert any("-S -m muvue._hook" in c and "MUVUE_HOOK_SCOPE" not in c for c in _commands(settings, event))


def test_the_user_install_prefixes_every_command_and_keeps_other_settings(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    path = adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    assert path == home / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    assert settings["theme"] == "dark"
    for event in SIX:
        assert all(c.startswith(adapters.USER_SCOPE_PREFIX) for c in _commands(settings, event))
    adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    again = json.loads(path.read_text())
    assert all(len(again["hooks"][event]) == 1 for event in SIX)  # idempotent


def test_doctor_reports_which_install_is_active(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_init.init_repo(repo)
    info = lambda: [i for i in doctor.run_doctor(repo, skip_security_probes=True).info if i.startswith("claude code hooks")]  # noqa: E731
    assert info() == ["claude code hooks: not installed"]
    adapters.install_claude_code(repo, protocol_version=PROTOCOL_VERSION)
    assert info() == ["claude code hooks: repo"]
    adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    assert info() == ["claude code hooks: repo, user"]


def test_doctor_warns_when_the_repo_install_lacks_the_session_events(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_init.init_repo(repo)
    path = adapters.install_claude_code(repo, protocol_version=PROTOCOL_VERSION)
    settings = json.loads(path.read_text())
    del settings["hooks"]["SessionEnd"]
    path.write_text(json.dumps(settings))
    report = doctor.run_doctor(repo, skip_security_probes=True)
    assert any("SessionEnd" in w and "adapter install claude-code" in w for w in report.warnings)
    assert report.ok


def test_a_user_install_survives_an_init_uninit_round_trip(tmp_path, home, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init_with_commit(repo)
    status = lambda: subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout  # noqa: E731
    before = status()

    repo_init.init_repo(repo)
    path = adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    repo_init.uninit_repo(repo)

    assert status() == before
    assert not (repo / ".muvue").exists()
    assert list(json.loads(path.read_text())["hooks"]) == SIX  # machine-wide: uninit leaves it
    # The left-behind user hook, in a repository that is no longer
    # registered, exits 0 and writes nothing.
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": str(repo)})))
    assert _hook.main(["session-start", str(repo)]) == 0
    assert not (repo / ".muvue").exists()


def test_the_cli_installs_for_the_user(tmp_path, home):
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "adapter", "install", "claude-code", "--user"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(json.loads((home / ".claude" / "settings.json").read_text())["hooks"]) == SIX


def test_the_cli_refuses_user_for_other_adapters(tmp_path, home):
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "adapter", "install", "codex", "--user"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "--user applies to claude-code only" in result.stderr
