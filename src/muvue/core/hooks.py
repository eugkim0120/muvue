"""Git hook business logic invoked by the shims `repo_init.py` installs
(plan section 5/7): `muvue hook post-commit` parses `Muvue-Node:`/`Refs:`
trailers (core.trailers), links the commit to any resolvable node in
`node_commits`, and (P7, drift loop items 1-2, plan section 9) flags an
unattributed commit that touches an anchored file straight to the
inbox. Real anchor-hash recompute/staleness marking (drift loop item 1)
needs actual git access to hash blobs at the commit, so it lives in
`handle_post_commit_from_git` (below) rather than here -- see that
function's docstring and `core.drift.mark_stale_for_commit`. P3 shipped
this hook enqueueing no-op `anchor.hash_requested`/`staleness.flagged`
signals for a future consumer to drain; those events are still recorded
(nothing here removes a previously-shipped event type) but P7 makes the
staleness marking they described real, not stubbed.

Every mutation here goes through `muvue.core` only (working rule 3): this
module issues its own SQL for `node_commits` (a table no other module
owns yet) but always through `record_event`/`conn` the same way
`nodes.py` does, never bypassing the events log.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from . import drift as drift_mod
from . import events as events_mod
from . import nodes as nodes_mod
from . import trailers


def handle_post_commit(
    conn: sqlite3.Connection,
    *,
    commit_sha: str,
    message: str,
    files: list[str] | None = None,
) -> dict:
    """Pure-data entry point (no subprocess/git access): parse `message`
    for node trailers, link `commit_sha` to every resolvable node, and
    enqueue anchor-hash/staleness signals for the ones that resolved.
    Unresolvable ids (the node doesn't exist, or was soft-deleted) are
    silently skipped -- trailers are labels, not trusted for binding."""
    node_ids = trailers.parse_node_ids(message)
    files_json = json.dumps(files or [])
    linked: list[int] = []
    for node_id in node_ids:
        try:
            node = nodes_mod.get_node(conn, node_id)
        except LookupError:
            continue
        if node["deleted_at"] is not None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO node_commits (node_id, sha, files) VALUES (?, ?, ?)",
            (node_id, commit_sha, files_json),
        )
        events_mod.record_event(
            conn,
            project_id=node["project_id"],
            node_id=node_id,
            actor="hook",
            type_="commit.linked",
            payload={"sha": commit_sha, "files": files or []},
        )
        linked.append(node_id)

    if linked:
        # No-op queue entries (P2 pattern): a future daemon consumer
        # drains these to compute real anchor hashes / staleness; for P3
        # this hook only has to enqueue the signal, not resolve it (plan
        # section 9 structure layer, P6+).
        events_mod.record_event(
            conn, project_id=None, node_id=None, actor="hook",
            type_="anchor.hash_requested",
            payload={"sha": commit_sha, "node_ids": linked},
        )
        events_mod.record_event(
            conn, project_id=None, node_id=None, actor="hook",
            type_="staleness.flagged",
            payload={"sha": commit_sha, "node_ids": linked},
        )
    # P7 drift loop item 2 (plan section 9): a commit with no
    # `Muvue-Node:`/`Refs:` trailer at all (`node_ids` empty -- not just
    # "nothing resolved") that touches a file a tracked component is
    # anchored to flags straight to the inbox, DB-only (no git access
    # needed: the anchor paths are already in `components.anchors_json`).
    drift_mod.flag_unattributed_commit(
        conn, commit_sha=commit_sha, files=files or [], node_ids=node_ids,
    )
    conn.commit()
    unresolved = [n for n in node_ids if n not in linked]
    return {"commit_sha": commit_sha, "linked_node_ids": linked, "unresolved_ids": unresolved}


def _git(repo_root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True,
    )
    return out.stdout


def handle_post_commit_from_git(conn: sqlite3.Connection, repo_root: Path) -> dict:
    """Real entry point for the installed shim: read HEAD's sha/message/
    touched files from git, delegate to `handle_post_commit` (trailer
    parsing, `node_commits` linking, unattributed-commit inbox flag),
    then (P7, drift loop item 1) recompute anchor hashes and mark stale
    every component whose anchored file this commit changed
    (`core.drift.mark_stale_for_commit`) -- the real git blob access
    `handle_post_commit` itself deliberately doesn't have, "within one
    commit" of the hand-edit landing (plan P7 acceptance #1)."""
    commit_sha = _git(repo_root, "rev-parse", "HEAD").strip()
    message = _git(repo_root, "log", "-1", "--pretty=%B", commit_sha)
    files_raw = _git(repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    files = [f for f in files_raw.splitlines() if f]
    result = handle_post_commit(conn, commit_sha=commit_sha, message=message, files=files)
    newly_stale = drift_mod.mark_stale_for_commit(conn, repo_root, commit_sha, files)
    conn.commit()
    result["newly_stale_component_ids"] = newly_stale
    return result
