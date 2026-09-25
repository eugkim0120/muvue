"""Merge / conflict handling (plan section 6 "Merging", P5): "Daemon merges
in dependency order. On conflict: node -> blocked(conflict), a 'rebase
onto main' subtask is auto-created for the same owner, attempts + 1."

Only meaningful in strict mode: each strict-mode node has its own git
worktree and branch (`node-<id>`, bound at `start` -- see `core.strict`).
Light mode has no separate per-node branch to merge -- an agent's commits
already land directly in the single tracked checkout, and a commit
trailer is only ever a label, never a binding (`core/trailers.py`) -- so
`attempt_merge` on a node with no bound worktree is a documented no-op
(see docs/decisions.md), matching `core.review.dispatch`'s own precedent
for "strict-mode-only, no-op otherwise".

Merging here means: merge the node's branch onto the *airlock's* `main`
(a local, muvue-owned scratch worktree of it, never `repo_root` itself --
"agent never holds a `main` checkout", plan section 5). Propagating the
airlock's `main` back into the human's own `repo_root` checkout, or out to
a real remote/PR, is out of P5 scope (`merge --pr` body generation is
explicitly P6, plan section 11) -- a human syncs from the airlock the same
way they'd pull from any other remote, muvue does not do this for them.
"""

from __future__ import annotations

import os
import subprocess
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import events as events_mod
from . import nodes as nodes_mod
from . import strict as strict_mod

MERGE_WORKTREE_NAME = "_merge_main"

# `git merge` (unlike `git worktree add`) creates a commit, which needs a
# committer identity -- this is a muvue-initiated merge, not a human's own
# `git commit`, so it must not depend on the ambient environment having
# `user.name`/`user.email` configured (a real deployment's daemon process
# may have none). `core.strict._run_git` doesn't take an env override (its
# own callers never commit), so this module has its own small wrapper
# instead of changing that shared helper's behavior for P4's callers.
_MERGE_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "muvue", "GIT_AUTHOR_EMAIL": "muvue@localhost",
    "GIT_COMMITTER_NAME": "muvue", "GIT_COMMITTER_EMAIL": "muvue@localhost",
}


def _run_git_as_muvue(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **_MERGE_COMMIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


class MergeError(Exception):
    """Raised on a genuine merge-machinery failure (e.g. the merge
    worktree itself can't be created) -- never raised for an ordinary
    merge *conflict*, which is the expected, handled `"conflict"` outcome
    (see `attempt_merge`'s docstring)."""


def _dependencies_pending(conn: sqlite3.Connection, node: sqlite3.Row) -> list[int]:
    """`depends_on` node ids that haven't merged yet. A dependency with no
    bound worktree (light mode, or never started under strict mode) is
    trivially "merged" -- there's no separate branch for it to merge."""
    dep_ids = [
        r["depends_on"]
        for r in db_mod.query_all(
            conn,
            "SELECT depends_on FROM deps WHERE node_id = ?",
            (node["id"],),
        )
    ]
    pending = []
    for dep_id in dep_ids:
        dep = nodes_mod.get_node(conn, dep_id)
        if dep["worktree"] is None:
            continue
        merged = db_mod.query_one(
            conn,
            "SELECT 1 FROM events WHERE node_id = ? AND type = 'merge.completed' LIMIT 1",
            (dep_id,),
        )
        if merged is None:
            pending.append(dep_id)
    return pending


def _merge_worktree(airlock: Path, repo_root: Path) -> Path:
    """The scratch worktree muvue merges into -- a *detached* checkout of
    `main`'s current commit, reused across calls. Detached, not on
    `refs/heads/main` itself, so `core.strict.ensure_airlock`'s own
    `main`-sync fetch (called by `bind_worktree` on every `start`) never
    conflicts with it -- git refuses to fetch into a branch checked out in
    another worktree. `main` is advanced explicitly afterwards, by
    `update-ref` (see `attempt_merge`), not by this worktree's own HEAD
    moving. Not any agent's per-node worktree either way; nothing here
    ever hands an agent a `main` checkout (plan section 5)."""
    root = strict_mod.worktrees_root(repo_root)
    path = root / MERGE_WORKTREE_NAME
    if not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        result = strict_mod._run_git(
            "--git-dir", str(airlock), "worktree", "add", "--detach", str(path), "main",
        )
        if result.returncode != 0:
            raise MergeError(f"failed to create merge worktree at {path}: {result.stderr}")
    else:
        strict_mod._run_git("checkout", "-q", "--detach", "main", cwd=path)
        strict_mod._run_git("reset", "-q", "--hard", "main", cwd=path)
    return path


def attempt_merge(
    conn: sqlite3.Connection, node_id: int, repo_root: str | Path, *, actor: str = "daemon"
) -> dict:
    """Attempt to merge one `done`, strict-mode node's branch onto the
    airlock's `main`, respecting dependency order.

    Returns one of:
      `{"status": "no_worktree"}` -- light mode, or the node never bound a
        strict-mode worktree; nothing to merge.
      `{"status": "already_merged"}`
      `{"status": "deferred", "pending_deps": [...]}` -- a dependency
        hasn't merged yet; call again once it has (see `merge_pending`,
        which does this in a loop).
      `{"status": "merged", "sha": ...}`
      `{"status": "conflict", "subtask": {...}}` -- node -> blocked
        (conflict), `attempts + 1`, a "rebase onto main" subtask created
        under it (plan section 6).
    """
    repo_root = Path(repo_root)
    node = nodes_mod.get_node(conn, node_id)
    if node["worktree"] is None:
        return {"status": "no_worktree"}
    if node["status"] != "done":
        raise MergeError(f"node {node_id} is status={node['status']!r}, not done")

    already = db_mod.query_one(
        conn,
        "SELECT 1 FROM events WHERE node_id = ? AND type = 'merge.completed' LIMIT 1",
        (node_id,),
    )
    if already is not None:
        return {"status": "already_merged"}

    pending = _dependencies_pending(conn, node)
    if pending:
        return {"status": "deferred", "pending_deps": pending}

    # The airlock must already exist by the time anything is `done` and
    # mergeable (some node's `start` created it).
    airlock = strict_mod.airlock_path(repo_root)
    if not airlock.exists():
        raise MergeError(
            f"no airlock found for {repo_root}; nothing has ever been started "
            "in strict mode, so there's nothing to merge"
        )
    merge_wt = _merge_worktree(airlock, repo_root)
    branch = f"node-{node_id}"
    result = _run_git_as_muvue("merge", "--no-ff", "--no-edit", branch, cwd=merge_wt)
    if result.returncode != 0:
        strict_mod._run_git("merge", "--abort", cwd=merge_wt)
        return _handle_conflict(conn, node, actor=actor)

    sha = strict_mod._run_git("rev-parse", "HEAD", cwd=merge_wt).stdout.strip()
    # The merge worktree is detached (see `_merge_worktree`), so the merge
    # commit it just made didn't move `refs/heads/main` on its own --
    # advance it explicitly now that the merge succeeded cleanly.
    update = strict_mod._run_git(
        "--git-dir", str(airlock), "update-ref", "refs/heads/main", sha,
    )
    if update.returncode != 0:
        raise MergeError(f"merged node {node_id} but failed to advance main: {update.stderr}")
    with db_mod.write_txn(conn):
        events_mod.record_event(
            conn, project_id=node["project_id"], node_id=node_id, actor=actor,
            actor_evidence="subprocess",
            type_="merge.completed", payload={"node_id": node_id, "sha": sha, "branch": branch},
        )
    return {"status": "merged", "sha": sha}


def _handle_conflict(conn: sqlite3.Connection, node: sqlite3.Row, *, actor: str) -> dict:
    with db_mod.write_txn(conn):
        nodes_mod.block(
            conn, node["id"], reason="conflict", actor=actor,
            bump_attempts=True, event_actor_role="daemon", actor_evidence="subprocess",
        )
        # "for the same owner" (plan section 6): not literally forced here --
        # the runner's own owner string is deterministic per routed agent name
        # (`f"runner:{agent_name}"`, core/runner.py), so re-scheduling this
        # subtask through the same `[routing]` kind naturally lands on the
        # same owner identity without needing a separate forced-assignment
        # mechanism (see docs/decisions.md).
        subtask = nodes_mod.create_node(
            conn, project_id=node["project_id"], parent_id=node["id"], kind="subtask",
            title=f"rebase onto main (node {node['id']})",
            body_md=(
                f"Merging node {node['id']}'s branch (node-{node['id']}) onto main "
                f"conflicted. Rebase node-{node['id']} onto the airlock's current main "
                "and resolve the conflicts; it can then be re-attempted."
            ),
            criteria=[f"node-{node['id']} merges onto main with no conflicts"],
            criteria_mode="auto", status="ready", actor=actor, actor_evidence="subprocess",
        )
        events_mod.record_event(
            conn, project_id=node["project_id"], node_id=node["id"], actor=actor,
            actor_evidence="subprocess",
            type_="merge.conflict", payload={"node_id": node["id"], "subtask_id": subtask["id"]},
        )
        return {"status": "conflict", "subtask": dict(subtask)}


def merge_pending(conn: sqlite3.Connection, repo_root: str | Path, *, project_id: int | None = None) -> list[dict]:
    """Attempt every `done`, unmerged, strict-mode-bound node, in
    dependency order: repeated passes over ascending node id until a full
    pass makes no further progress. `deps` chains in this codebase are
    shallow (spec -> task -> subtask, plan section 3), so a couple of
    passes suffice without needing a real topological sort."""
    results: list[dict] = []
    seen_final: set[int] = set()
    progressed = True
    while progressed:
        progressed = False
        query = (
            "SELECT id FROM nodes WHERE status = 'done' AND worktree IS NOT NULL "
            "AND deleted_at IS NULL"
        )
        params: tuple = ()
        if project_id is not None:
            query += " AND project_id = ?"
            params = (project_id,)
        query += " ORDER BY id"
        candidates = [r["id"] for r in db_mod.query_all(conn, query, params)]
        for node_id in candidates:
            if node_id in seen_final:
                continue
            outcome = attempt_merge(conn, node_id, repo_root)
            if outcome["status"] != "deferred":
                seen_final.add(node_id)
                results.append({"node_id": node_id, **outcome})
                progressed = True
    return results
