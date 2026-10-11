"""Mission control phase 1: what the Claude Code hooks spool for
sessions. Golden queue lines for every event, the PreToolUse line, the
two record-only events, a linked worktree outside the repository, the
user-level install, and `hook_error`."""

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
from muvue.core import adapters
from muvue.core.repo_init import init_repo

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
SESSION = {"session_id": "s-1", "cwd": "/work/app", "transcript_path": "/t/s-1.jsonl"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # This suite may itself run inside a Claude Code or muvue-launched
    # session; none of that may leak into the lines under test.
    for name in ("MUVUE_SESSION", "MUVUE_HOOK_SCOPE", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    init_repo(root)
    return root


def _queue_lines(repo_root: Path) -> list[dict]:
    path = repo_root / ".muvue" / "queue.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _main(monkeypatch, name: str, path: Path, payload: dict) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    return _hook.main([name, str(path)])


# -- golden lines ----------------------------------------------------------


def test_a_stop_line_carries_the_session(repo):
    _hook.run("stop", str(repo), {**SESSION, "stop_hook_active": True})
    lines = _queue_lines(repo)
    assert lines == [{"event": "stop", "ts": lines[0]["ts"], "node_id": None, **SESSION, "agent": "claude"}]


def test_pre_tool_use_appends_one_line_per_call_with_the_edited_file(repo):
    _hook.run("pre-tool-use", str(repo), {**SESSION, "tool_name": "Edit", "tool_input": {"file_path": "/work/app/a.py", "old_string": "x"}})
    _hook.run("pre-tool-use", str(repo), {**SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}})
    lines = _queue_lines(repo)
    assert lines == [
        {"event": "pre-tool-use", "ts": lines[0]["ts"], "node_id": None, "tool_name": "Edit",
         "file_path": "/work/app/a.py", **SESSION, "agent": "claude"},
        {"event": "pre-tool-use", "ts": lines[1]["ts"], "node_id": None, "tool_name": "Bash",
         **SESSION, "agent": "claude"},
    ]


def test_pre_tool_use_without_a_session_spools_nothing(repo):
    _hook.run("pre-tool-use", str(repo), {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert _queue_lines(repo) == []


def test_pre_compact_keeps_its_summary(repo):
    _hook.run("pre-compact", str(repo), {**SESSION, "summary": "did things"})
    lines = _queue_lines(repo)
    assert lines == [{"event": "pre-compact", "ts": lines[0]["ts"], "node_id": None, "summary": "did things",
                      **SESSION, "agent": "claude"}]


@pytest.mark.parametrize("name, extra, expected", [
    ("user-prompt-submit", {"prompt": "do it"}, {}),
    ("session-end", {"reason": "logout"}, {"reason": "logout"}),
])
def test_record_only_events_spool_and_allow(repo, name, extra, expected):
    outcome = _hook.run(name, str(repo), {**SESSION, **extra})
    assert (outcome.exit_code, outcome.stdout, outcome.stderr) == (0, "", "")
    lines = _queue_lines(repo)
    # The prompt text itself is never spooled.
    assert lines == [{"event": name, "ts": lines[0]["ts"], "node_id": None, **SESSION, "agent": "claude", **expected}]


def test_a_launched_agent_names_its_run(repo, monkeypatch):
    monkeypatch.setenv("MUVUE_SESSION", "run-abc")
    _hook.run("session-start", str(repo), SESSION)
    assert _queue_lines(repo)[0]["launched_session"] == "run-abc"


# -- finding the repository from a worktree --------------------------------


def test_a_hook_in_a_linked_worktree_records_into_the_owning_repo_and_never_blocks(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "feat", str(wt)], cwd=root, check=True, capture_output=True)

    payload = {**SESSION, "cwd": str(wt), "tool_name": "Edit", "tool_input": {"file_path": str(wt / "a.py")}}
    # In the main checkout an Edit with no active node is blocked (exit 2);
    # outside it the hook only records.
    assert _main(monkeypatch, "pre-tool-use", wt, payload) == 0
    assert [line["event"] for line in _queue_lines(root)] == ["pre-tool-use"]
    assert not (wt / ".muvue").exists()


def test_a_strict_mode_worktree_resolves_through_the_airlock_marker(tmp_path):
    root = tmp_path / "repo"
    (root / ".muvue").mkdir(parents=True)
    airlock = tmp_path / "airlock.git"
    (airlock / "worktrees" / "node-3").mkdir(parents=True)
    (airlock / "muvue-repo-root").write_text(str(root))
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {airlock / 'worktrees' / 'node-3'}\n")
    assert _hook._find_worktree_repo_root(str(wt / "src")) == str(root)


def test_a_worktree_of_an_uninitialised_repo_is_ignored(tmp_path):
    root = tmp_path / "plain"
    (root / ".git" / "worktrees" / "x").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {root / '.git' / 'worktrees' / 'x'}\n")
    assert _hook._find_worktree_repo_root(str(wt)) is None


# -- user-level install ----------------------------------------------------


def test_user_scope_hooks_only_record(repo, monkeypatch):
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    payload = {**SESSION, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "a.py")}}
    assert _main(monkeypatch, "pre-tool-use", repo, payload) == 0
    assert [line["event"] for line in _queue_lines(repo)] == ["pre-tool-use"]


def test_user_scope_defers_to_the_projects_own_hooks(repo, monkeypatch):
    adapters.install_claude_code(repo, protocol_version=3)
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    assert _main(monkeypatch, "stop", repo, SESSION) == 0
    assert _queue_lines(repo) == []


def test_user_scope_outside_a_registered_repo_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _main(monkeypatch, "session-start", plain, SESSION) == 0
    assert not (plain / ".muvue").exists()


# -- errors -----------------------------------------------------------------


def test_a_crashing_hook_spools_hook_error_and_lets_the_agent_through(repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_hook, "run", boom)
    assert _main(monkeypatch, "stop", repo, SESSION) == 0
    lines = _queue_lines(repo)
    assert lines == [{"event": "hook_error", "ts": lines[0]["ts"], "hook": "stop", "error": "RuntimeError: boom"}]


# -- hot path ---------------------------------------------------------------


def test_pre_tool_use_capture_opens_no_sqlite_and_no_subprocess(repo):
    payload = json.dumps({**SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}})
    code = (
        "import io, json, sys\n"
        f"sys.stdin = io.StringIO({payload!r})\n"
        "import muvue._hook as h\n"
        f"rc = h.main(['pre-tool-use', {str(repo)!r}])\n"
        "print(json.dumps({'exit': rc, 'sqlite3': 'sqlite3' in sys.modules, "
        "'subprocess': 'subprocess' in sys.modules}))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUVUE_", "CLAUDE_"))}
    env["PYTHONPATH"] = str(SRC_DIR)
    out = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"exit": 0, "sqlite3": False, "subprocess": False}
    assert [line["event"] for line in _queue_lines(repo)] == ["pre-tool-use"]


def test_the_cli_hook_command_accepts_the_new_events(repo):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUVUE_", "CLAUDE_"))}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "user-prompt-submit", str(repo)],
        input=json.dumps(SESSION), capture_output=True, text=True, cwd=repo, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert _queue_lines(repo)[-1]["event"] == "user-prompt-submit"
