from __future__ import annotations

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
