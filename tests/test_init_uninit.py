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


def test_init_gitignore_skips_lines_already_present_as_plain_text(tmp_path: Path):
    """Dogfood-gate follow-up (docs/decisions.md #40): a repo whose
    .gitignore already has a plain `.muvue/muvue.db` line (no marker
    block) shouldn't get that line duplicated inside the marker block
    `init` adds."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".muvue/muvue.db\nnode_modules/\n")

    repo_init.init_repo(repo)

    lines = (repo / ".gitignore").read_text().splitlines()
    assert lines.count(".muvue/muvue.db") == 1
    assert "node_modules/" in lines
    # Entries not already present still get added inside the marker block.
    assert ".muvue/session" in lines

    repo_init.uninit_repo(repo)
