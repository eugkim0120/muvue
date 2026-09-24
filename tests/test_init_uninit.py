"""P0 acceptance #3: `init` then `uninit` leaves `git status` clean on all
4 fixture repos (plain Python, JS+Husky, docs-only, monorepo)."""

import subprocess
from pathlib import Path

from muvue.core import repo_init


def _git_status_porcelain(repo: Path) -> str:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout


def test_init_then_uninit_leaves_git_status_clean(git_fixture_repo: Path):
    repo = git_fixture_repo
    assert _git_status_porcelain(repo) == ""

    muvue_dir = repo_init.init_repo(repo)
    assert muvue_dir.exists()
    # init creates untracked/modified state (this is expected and reverted below).

    repo_init.uninit_repo(repo)

    assert not (repo / ".muvue").exists()
    assert _git_status_porcelain(repo) == ""


def test_init_installs_absolute_path_hook_shims(git_fixture_repo: Path):
    import sys

    repo = git_fixture_repo
    repo_init.init_repo(repo)
    husky_dir = repo / ".husky"
    for name in repo_init.HOOK_NAMES:
        path = husky_dir / name if husky_dir.is_dir() else repo / ".git" / "hooks" / name
        content = path.read_text()
        assert sys.executable in content
        assert "-m muvue hook" in content
    repo_init.uninit_repo(repo)
