"""`init` / `uninit` ops verbs: scaffold .muvue/, install git-hook shims,
and reverse the process byte-for-byte.

v4 section 11's P0 acceptance row is stricter than v3's ("`git status`
clean"): "`init` then `uninit` leaves no muvue-owned tracked or untracked
files and `git status` matches the pre-`init` baseline on all 4 fixtures."
`git status --porcelain` alone can't catch a leftover *gitignored* file
(it never shows in porcelain output), so this module snapshots the real
on-disk file listing before `init` and asserts it is byte-identical after
`uninit` -- not just "clean by git's definition." See docs/decisions.md
for the `.muvue/muvue.db-wal`/`-shm` sqlite-with-open-connection exclusion
and the `muvue/structure` git-ref cleanup-on-uninit decision this module
exercises for real.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import close as close_mod
from muvue.core import db as db_mod
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core import repo_init

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# sqlite writes `-wal`/`-shm` companion files alongside `muvue.db` only
# while a connection is open in WAL mode; they're transient sqlite
# housekeeping, not something `init`/`uninit` themselves create or must
# account for, and they're gone once every connection this test opens is
# closed -- excluded here for the same reason `.git/index`/`.git/objects`
# are: git (or here, sqlite) manages them, muvue's manifest doesn't.
_TRANSIENT_SQLITE_SUFFIXES = ("-wal", "-shm", "-journal")


def _git_status_porcelain(repo: Path) -> str:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout


def _snapshot(repo_root: Path) -> dict[str, str]:
    """Every file on disk under `repo_root`, tracked or untracked or
    gitignored -- real filesystem state, not git's view of it (that's
    the whole point of v4's tightened wording, see module docstring).

    `.git/` is walked too -- `init` appends to `.git/hooks/*` shims (when
    there's no `.husky/`) and `uninit` must restore them, so that's very
    much muvue's concern -- but the internals git itself owns and mutates
    on every commit/gc regardless of muvue (`.git/index`, `.git/objects`,
    `.git/refs`, `.git/logs`, `.git/COMMIT_EDITMSG`, ...) are excluded, or
    this snapshot would need to be identical to a plain `git commit`'s own
    side effects too, which has nothing to do with `uninit`. The one
    `.git/refs` exception -- `refs/heads/muvue/structure` -- IS muvue's
    concern (it's the ref `core.close` creates) and is deliberately left
    IN the snapshot so a leftover structure ref shows up as a real diff.
    """
    result: dict[str, str] = {}
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        parts = rel.parts
        posix = rel.as_posix()
        if parts[0] == ".git":
            if len(parts) >= 2 and parts[1] in (
                "index", "objects", "logs", "COMMIT_EDITMSG", "ORIG_HEAD",
            ):
                continue
            if posix.startswith(".git/refs/") and posix != ".git/refs/heads/muvue/structure":
                continue
        if path.name.endswith(_TRANSIENT_SQLITE_SUFFIXES):
            continue
        try:
            result[rel.as_posix()] = path.read_text()
        except (UnicodeDecodeError, OSError):
            result[rel.as_posix()] = f"<binary:{path.stat().st_size} bytes>"
    return result


def _diff_snapshots(before: dict[str, str], after: dict[str, str]) -> str:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    lines = []
    if added:
        lines.append(f"added: {added}")
    if removed:
        lines.append(f"removed: {removed}")
    if changed:
        lines.append(f"changed: {changed}")
    return "\n".join(lines)


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
    repo = git_fixture_repo
    repo_init.init_repo(repo)
    husky_dir = repo / ".husky"
    for name in repo_init.HOOK_NAMES:
        path = husky_dir / name if husky_dir.is_dir() else repo / ".git" / "hooks" / name
        content = path.read_text()
        assert sys.executable in content
        # v4 section 4a (P0.5): shims invoke the stdlib-only fast path,
        # not the full Typer CLI -- see src/muvue/_hook.py.
        assert "-S -m muvue._hook" in content
        assert name in content
    repo_init.uninit_repo(repo)


def test_init_gitignore_skips_lines_already_present_as_plain_text(tmp_path: Path):
    """Dogfood-gate follow-up (docs/decisions.md #40): a repo whose
    .gitignore already has a plain `.muvue/muvue.db` line (no marker
    block) shouldn't get that line duplicated inside the marker block
    `init` adds."""
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


# --- v4 section 11 P0 acceptance: real filesystem-snapshot round-trip ---


def _run_hook(repo: Path, name: str) -> None:
    """Invoke the real hook shim (`muvue._hook NAME`) against `repo` the
    same way a git hook shim installed by `init` would run it -- shells
    out to `sh -c` since `hook_fast_path_command` returns a shell command
    string (`PYTHONPATH=... <py> -S -m muvue._hook NAME`), same as the
    installed shim itself. Proves `uninit` also cleans up state a hook
    actually wrote (`.muvue/queue.jsonl`), not just a freshly-`init`'d
    empty tree."""
    command = repo_init.hook_fast_path_command(name) + " " + shlex.quote(str(repo))
    subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=repo, check=True, capture_output=True, text=True, input="",
    )


def _exercise_usage(repo: Path) -> None:
    """Create a project + node, commit, run the post-commit hook, and
    write a real structure commit onto `muvue/structure` (v4 section 9) --
    proves `uninit` cleans up genuinely-used state, including the
    structure ref, not just an empty `init`."""
    conn = db_mod.connect(repo / ".muvue" / "muvue.db")
    try:
        project = projects_mod.create_project(conn, goal="snapshot-test project", repo_root=repo)
        nodes_mod.create_node(
            conn, project_id=project["id"], kind="task", title="do a thing",
        )
    finally:
        conn.close()

    (repo / "touched.txt").write_text("touched by the snapshot test\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "snapshot-test commit"],
        cwd=repo, check=True, capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
        },
    )
    _run_hook(repo, "post-commit")

    close_mod._write_structure_commit(
        repo, {".muvue/components.json": "[]\n"}, "snapshot-test structure commit"
    )
    assert (repo / ".git" / "refs" / "heads" / "muvue" / "structure").exists()

    # Revert the commit this helper made so the *tracked* tree matches the
    # pre-init baseline again -- this test is about muvue-owned artifacts,
    # not about muvue also being able to undo a human's own commit.
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=repo, check=True, capture_output=True)


@pytest.mark.parametrize("fixture_name", ["plain_python", "js_husky", "docs_only", "monorepo"])
def test_init_uninit_filesystem_snapshot_roundtrip(tmp_path: Path, fixture_name: str):
    from conftest import make_git_fixture

    repo = make_git_fixture(tmp_path, fixture_name)
    before = _snapshot(repo)

    repo_init.init_repo(repo)
    _exercise_usage(repo)
    repo_init.uninit_repo(repo)

    after = _snapshot(repo)

    diff = _diff_snapshots(before, after)
    assert after == before, (
        f"fixture={fixture_name!r}: uninit left the filesystem different from the "
        f"pre-init baseline:\n{diff}"
    )
    assert _git_status_porcelain(repo) == ""
    assert not (repo / ".git" / "refs" / "heads" / "muvue" / "structure").exists()
