"""Small git subprocess helper for the branch-coherence check (v4 section
5, changelog item 12): `git rev-parse --abbrev-ref HEAD` now has three
call sites -- `core.projects.create_project` (records the branch a
project started on), `core.nodes.start` (compares it on every start), and
`core.doctor.run_doctor` (same comparison for `doctor`) -- so it lives
here once instead of being copy-pasted three times, the same way
`core.strict`/`core.hooks` each keep their own local `_run_git` for a
single call site.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def current_branch(repo_root: Path) -> str | None:
    """The branch checked out in `repo_root`'s own working tree -- never
    a per-node strict-mode worktree's (those are intentionally on their
    own `node-<id>` branch; see `core.nodes.start`'s branch-coherence
    check for why that distinction matters). Returns `None` if it can't
    be determined (not a git repo, `git` not on PATH). A detached HEAD
    prints the literal string `"HEAD"`, which is passed through as-is: it
    simply never matches a recorded branch name, which is the correct
    "diverged" outcome rather than a special case."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=Path(repo_root), capture_output=True, text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def head_sha(repo_root: Path) -> str | None:
    """`HEAD`'s commit sha in `repo_root`, or None (not a repo, no commit)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=Path(repo_root), capture_output=True, text=True,
        )
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def worktree_top(start: str) -> str | None:
    """The nearest directory at or above `start` holding a `.git` entry (a
    directory for a main checkout, a file for a linked worktree). No
    subprocess: the queue drain calls this for every touched path."""
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def branch_at(start: str) -> str | None:
    """The branch checked out in the worktree containing `start`, read
    from its HEAD file. None when detached, or not inside a git worktree."""
    top = worktree_top(start)
    if top is None:
        return None
    git_dir = os.path.join(top, ".git")
    if os.path.isfile(git_dir):
        try:
            with open(git_dir) as f:
                first = f.readline().strip()
        except (OSError, ValueError):  # ValueError: NUL in the path
            return None
        if not first.startswith("gitdir:"):
            return None
        git_dir = first[len("gitdir:"):].strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(top, git_dir)
    try:
        with open(os.path.join(git_dir, "HEAD")) as f:
            head = f.read().strip()
    except (OSError, ValueError):  # ValueError: NUL in the gitdir path
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None
