"""Strict mode (plan section 5 "Strict mode" paragraph, P4): airlock bare
repo, one git worktree per node bound at `start`, `worktree_setup`, and the
`pre-receive` hook that enforces the worktree->node binding on push.

"Trailers ... are labels; the worktree binding is trusted." (plan section
5) -- in light mode a commit trailer is the only link between a commit and
a node, and it's just parsed text (core/trailers.py). Strict mode instead
binds a real git branch (`node-<id>`) to a node at `start` and enforces,
server-side, in the airlock's `pre-receive` hook, that only a push to a
branch backing an active binding is accepted. Per plan section 13
(residual risks) and section 5's own framing: this is accidental- and
lazy-bypass protection, not adversarial isolation -- pre-receive can see
which ref is being updated and check DB state, but it cannot verify which
process or filesystem path a push actually came from (same-machine, same-
user git has no such identity boundary). Documented, not oversold.

Airlock path format (plan section 2 file layout table:
`~/.muvue/airlocks/<repo-hash>.git`): `<repo-hash>` is the first 16 hex
chars of sha256(str(repo_root.resolve())) -- the repo's absolute path, not
its git remote/init identity, since a repo may have no remote at all (see
docs/decisions.md).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from muvue import _hook

REPO_ROOT_MARKER = "muvue-repo-root"
UPSTREAM_REF = "refs/muvue/upstream"


class StrictModeError(Exception):
    """Raised when strict-mode git mechanics fail: worktree creation,
    `worktree_setup`, or a rejected pre-receive push. Callers of
    `bind_worktree` must treat this as "nothing happened" -- see its
    docstring for the cleanup guarantee."""


def _repo_hash(repo_root: Path) -> str:
    return hashlib.sha256(str(Path(repo_root).resolve()).encode()).hexdigest()[:16]


def airlocks_dir() -> Path:
    return Path.home() / ".muvue" / "airlocks"


def airlock_path(repo_root: Path) -> Path:
    return airlocks_dir() / f"{_repo_hash(repo_root)}.git"


def worktrees_root(repo_root: Path) -> Path:
    return Path.home() / ".muvue" / "worktrees" / _repo_hash(repo_root)


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
    )


def _install_pre_receive_shim(airlock: Path) -> None:
    hooks_dir = airlock / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-receive"
    from .repo_init import hook_fast_path_command

    hook_path.write_text(f"#!/bin/sh\n{hook_fast_path_command('pre-receive')}\n")
    hook_path.chmod(hook_path.stat().st_mode | 0o111)


def ensure_airlock(repo_root: Path) -> Path:
    """Create (if missing) the bare repo backing strict mode for
    `repo_root`, install its `pre-receive` shim, and sync `main` from
    `repo_root`'s current HEAD so worktrees branched off it start from
    real content. Safe to call repeatedly: `main` fast-forwards to the
    checkout's HEAD when it can and is otherwise left as is (see
    `_advance_main_to_upstream`)."""
    repo_root = Path(repo_root).resolve()
    airlock = airlock_path(repo_root)
    if not airlock.exists():
        airlock.parent.mkdir(parents=True, exist_ok=True)
        result = _run_git("init", "--bare", "-q", "-b", "main", str(airlock))
        if result.returncode != 0:
            raise StrictModeError(f"failed to create airlock {airlock}: {result.stderr}")
        (airlock / REPO_ROOT_MARKER).write_text(str(repo_root))
    _install_pre_receive_shim(airlock)

    # A *fetch* (airlock pulling from repo_root), not a push -- pre-receive
    # only fires on the receiving end of a push, and refs/heads/main is
    # intentionally rejected there (see evaluate_ref_update). The fetch
    # lands on a side ref, never directly on `main`: `core.merge` advances
    # the airlock's `main` with merge commits that `repo_root` doesn't
    # have, and a forced `+HEAD:refs/heads/main` here used to rewind them
    # on every later `start`.
    fetch = _run_git(
        "--git-dir", str(airlock), "fetch", str(repo_root), f"+HEAD:{UPSTREAM_REF}",
    )
    if fetch.returncode != 0:
        raise StrictModeError(
            f"failed to sync main into airlock {airlock}: {fetch.stderr}"
        )
    _advance_main_to_upstream(airlock)
    return airlock


def _advance_main_to_upstream(airlock: Path) -> None:
    """Seed `main` from the fetched checkout HEAD when missing, and
    fast-forward it when the checkout moved ahead. When `main` already
    contains the checkout HEAD (merges ahead) or the two have diverged,
    `main` is left alone: merge commits are never discarded."""
    git_dir = ("--git-dir", str(airlock))
    upstream = _run_git(*git_dir, "rev-parse", "--verify", "-q", UPSTREAM_REF).stdout.strip()
    main = _run_git(*git_dir, "rev-parse", "--verify", "-q", "refs/heads/main").stdout.strip()
    if main == upstream:
        return
    if main:
        is_ff = _run_git(*git_dir, "merge-base", "--is-ancestor", main, upstream).returncode == 0
        if not is_ff:
            return
    update = _run_git(*git_dir, "update-ref", "refs/heads/main", upstream)
    if update.returncode != 0:
        raise StrictModeError(f"failed to advance airlock main: {update.stderr}")


def bind_worktree(node, config, repo_root: Path) -> Path:
    """Create (or return the existing) per-node worktree for `node`,
    branched off the airlock's `main`, and run `config.worktree_setup`
    once in it. Returns the worktree path.

    On any failure -- worktree creation or `worktree_setup` -- the
    worktree and its branch are torn back down before raising
    `StrictModeError` with the failing command's output attached, so
    `nodes.start` can fail the whole call without leaving a half-bound
    node or an orphan worktree on disk (see nodes.start's ordering:
    binding happens before the DB transition is applied)."""
    repo_root = Path(repo_root)
    existing = node["worktree"]
    if existing is not None:
        existing_path = Path(existing)
        if not existing_path.exists():
            raise StrictModeError(
                f"node {node['id']} is bound to worktree {existing}, which no longer "
                "exists on disk; run `muvue doctor --repair`"
            )
        return existing_path

    airlock = ensure_airlock(repo_root)
    branch = f"node-{node['id']}"
    wt_root = worktrees_root(repo_root)
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = wt_root / branch
    if wt_path.exists():
        raise StrictModeError(f"worktree path {wt_path} already exists for node {node['id']}")

    add = _run_git(
        "--git-dir", str(airlock), "worktree", "add", str(wt_path), "-b", branch, "main",
    )
    if add.returncode != 0:
        raise StrictModeError(
            f"failed to create worktree for node {node['id']}: {add.stderr}"
        )

    _run_worktree_setup(
        config, wt_path, node["id"], lambda: _teardown_worktree(airlock, wt_path, branch),
    )
    return wt_path


def _run_worktree_setup(config, wt_path: Path, node_id: int, teardown) -> None:
    """Run `worktree_setup` once in a fresh worktree; on failure tear the
    worktree down and raise with the command's output attached."""
    setup_cmd = (config.worktree_setup or "").strip()
    if not setup_cmd:
        return
    setup = subprocess.run(setup_cmd, shell=True, cwd=wt_path, capture_output=True, text=True)
    if setup.returncode != 0:
        teardown()
        raise StrictModeError(
            f"worktree_setup ({setup_cmd!r}) failed for node {node_id} "
            f"(exit {setup.returncode}):\n"
            f"--- stdout ---\n{setup.stdout}\n--- stderr ---\n{setup.stderr}"
        )


def bind_light_worktree(node, config, repo_root: Path) -> Path:
    """Light mode with `worktree_mode = "per_node"` (v4 section 6): a
    worktree of the user's own repository on branch `node-<id>`, off the
    current HEAD, so parallel agents never share a checkout. No airlock:
    light mode trusts the user's repo. `core.merge` merges the branch
    back into the checkout."""
    repo_root = Path(repo_root)
    existing = node["worktree"]
    if existing is not None:
        if not Path(existing).exists():
            raise StrictModeError(
                f"node {node['id']} is bound to worktree {existing}, which no longer "
                "exists on disk; run `muvue doctor --repair`"
            )
        return Path(existing)
    branch = f"node-{node['id']}"
    wt_root = worktrees_root(repo_root)
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = wt_root / branch
    if wt_path.exists():
        raise StrictModeError(f"worktree path {wt_path} already exists for node {node['id']}")
    add = _run_git("worktree", "add", "-q", str(wt_path), "-b", branch, "HEAD", cwd=repo_root)
    if add.returncode != 0:
        raise StrictModeError(f"failed to create worktree for node {node['id']}: {add.stderr}")

    def teardown() -> None:
        _run_git("worktree", "remove", "--force", str(wt_path), cwd=repo_root)
        _run_git("branch", "-D", branch, cwd=repo_root)

    _run_worktree_setup(config, wt_path, node["id"], teardown)
    return wt_path


def is_airlock_worktree(worktree: str | Path, repo_root: Path) -> bool:
    """True when `worktree` belongs to the strict-mode airlock, False when
    it is a light-mode worktree of the user's own repository."""
    common = _run_git("rev-parse", "--git-common-dir", cwd=Path(worktree))
    if common.returncode != 0:
        return False
    return (Path(worktree) / common.stdout.strip()).resolve() == airlock_path(repo_root).resolve()


def _teardown_worktree(airlock: Path, wt_path: Path, branch: str) -> None:
    _run_git("--git-dir", str(airlock), "worktree", "remove", "--force", str(wt_path))
    _run_git("--git-dir", str(airlock), "branch", "-D", branch)


def worktree_diff_files(worktree: str | Path, *, base: str = "main") -> list[str]:
    """Real `git diff --name-only` from the node's worktree against its
    branch point on `base` (plan section 5: "now that real diffs are
    available from real worktrees, vs light mode's `predicted_touches`-
    based proxy"). Three-dot diff (against the merge-base) so it reflects
    only the node branch's own commits, not any unrelated drift `base`
    picked up meanwhile."""
    result = _run_git("diff", "--name-only", f"{base}...HEAD", cwd=Path(worktree))
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


# -- pre-receive (plan section 5: the load-bearing enforcement for P4
# acceptance criterion 1) --------------------------------------------------

def evaluate_ref_update(conn, ref_name: str) -> tuple[bool, str]:
    """Return (accepted, reason) for one pushed ref. The rule lives in
    the stdlib-only `muvue._hook`, which the airlock's shim runs."""
    return _hook.evaluate_ref_update(conn, ref_name)


def handle_pre_receive(conn, lines: list[str]) -> tuple[bool, list[str]]:
    """`lines` are raw `<old> <new> <ref>` pre-receive lines. Returns
    (accept_all, messages): git pre-receive is all-or-nothing per push."""
    return _hook.evaluate_ref_updates(conn, lines)


def handle_pre_receive_cli(cwd: Path) -> int:
    """`muvue hook pre-receive`, the full-CLI twin of the airlock shim."""
    outcome = _hook.pre_receive(str(cwd), sys.stdin.read().splitlines())
    sys.stderr.write(outcome.stderr)
    return outcome.exit_code
