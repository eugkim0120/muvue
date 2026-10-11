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
from dataclasses import dataclass
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


class GitError(Exception):
    """A git command failed. The message is `git failed: <first line of
    stderr>`, shown as is on the dashboard row it concerns."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=Path(repo_root), capture_output=True, text=True)
    except OSError as e:
        raise GitError(f"git failed: {e}") from e
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise GitError(f"git failed: {lines[0] if lines else f'exit {result.returncode}'}")
    return result.stdout


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str | None
    branch: str | None  # short name; None when detached or bare
    bare: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False


def parse_worktree_porcelain(text: str) -> list[Worktree]:
    """`git worktree list --porcelain`: blank-line separated records of
    `key value` lines (`worktree`, `HEAD`, `branch`) and bare flags
    (`bare`, `detached`, `locked [reason]`, `prunable [reason]`)."""
    found = []
    for block in text.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        found.append(Worktree(
            path=fields["worktree"], head=fields.get("HEAD"),
            branch=branch.removeprefix("refs/heads/") if branch else None,
            bare="bare" in fields, detached="detached" in fields,
            locked="locked" in fields, prunable="prunable" in fields,
        ))
    return found


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """Every worktree of the repository at `repo_root` (a checkout or a
    bare repository), main worktree first."""
    return parse_worktree_porcelain(_git(repo_root, "worktree", "list", "--porcelain"))


def rev_parse(repo_root: Path, ref: str) -> str:
    return _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


@dataclass(frozen=True)
class BranchStatus:
    ahead: int
    behind: int
    changed_paths: tuple[str, ...]
    fork_sha: str
    fork_time: int  # unix seconds
    tip_time: int


_STATUS_CACHE: dict[tuple[str, str, str], BranchStatus] = {}
_STATUS_CACHE_MAX = 256


def branch_status(repo_root: Path, *, base_sha: str, tip_sha: str) -> BranchStatus:
    """Commits ahead of and behind the base, the paths changed since the
    fork point (`git diff --name-only base...tip`), and the fork and tip
    commit times. Keyed by sha, not branch name: an unchanged worktree is
    answered from the cache without spawning git, so a dashboard refresh
    costs nothing for branches that did not move."""
    key = (str(Path(repo_root).resolve()), tip_sha, base_sha)
    cached = _STATUS_CACHE.get(key)
    if cached is not None:
        return cached
    behind, ahead = (int(n) for n in _git(repo_root, "rev-list", "--left-right", "--count", f"{base_sha}...{tip_sha}").split())
    changed = tuple(p for p in _git(repo_root, "diff", "--name-only", f"{base_sha}...{tip_sha}").splitlines() if p)
    fork_sha = _git(repo_root, "merge-base", base_sha, tip_sha).strip()
    fork_time = int(_git(repo_root, "log", "-1", "--format=%ct", fork_sha).strip())
    tip_time = int(_git(repo_root, "log", "-1", "--format=%ct", tip_sha).strip())
    status = BranchStatus(ahead, behind, changed, fork_sha, fork_time, tip_time)
    if len(_STATUS_CACHE) >= _STATUS_CACHE_MAX:
        _STATUS_CACHE.pop(next(iter(_STATUS_CACHE)))
    _STATUS_CACHE[key] = status
    return status
