from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FIXTURE_NAMES = ["plain_python", "js_husky", "docs_only", "monorepo"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
        },
    )


def make_git_fixture(tmp_path: Path, name: str) -> Path:
    """Copy tests/fixtures/<name> into tmp_path, git-init it, and commit
    everything so `git status --porcelain` starts clean."""
    src = FIXTURES_DIR / name
    dst = tmp_path / name
    shutil.copytree(src, dst)
    _git(dst, "init", "-q", "-b", "main")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-q", "-m", "initial commit")
    return dst


@pytest.fixture(params=FIXTURE_NAMES)
def git_fixture_repo(tmp_path, request) -> Path:
    return make_git_fixture(tmp_path, request.param)


def use_passing_checks(repo_root: Path) -> None:
    """`done` runs `[checks] test` and `lint` itself (v4 section 5). A
    temp repo has no test suite (and this host may lack ruff), so tests
    that expect an auto-approved `done` through the CLI point both
    commands at `true`."""
    config_path = repo_root / ".muvue" / "config.toml"
    text = config_path.read_text()
    for line in ('test = "pytest -q"', 'lint = "ruff check ."'):
        assert line in text, f"fixture assumes DEFAULT_CONFIG_TOML has {line!r}"
    text = text.replace('test = "pytest -q"', 'test = "true"').replace(
        'lint = "ruff check ."', 'lint = "true"'
    )
    config_path.write_text(text)


def git_init_with_commit(repo: Path) -> None:
    """A git repo with one commit, so `worktree_mode = "per_node"` has a
    HEAD to branch nodes off."""
    env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "initial"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """CLI output without terminal styling. Rich colours Typer's help and
    errors when it detects CI (e.g. `GITHUB_ACTIONS`), which splits a flag
    such as `--request-id` across escape codes."""
    return _ANSI.sub("", text)
