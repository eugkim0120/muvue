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
