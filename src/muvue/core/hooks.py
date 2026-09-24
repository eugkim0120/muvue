"""Git hook business logic invoked by the shims `repo_init.py` installs
(plan section 5/7): `muvue hook post-commit` parses `Muvue-Node:`/`Refs:`
trailers (core.trailers), links the commit to any resolvable node in
`node_commits`, and enqueues the two P3 no-op signals the plan lists for
this hook -- anchor-hashing and staleness -- as `events` rows a future
consumer will drain (same documented no-op-queue pattern P2's
`core.daemon.process_queue` already established; real anchor-hashing is
structure-layer work, explicitly P6+, see docs/decisions.md).

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
    touched files from git and delegate to `handle_post_commit`."""
    commit_sha = _git(repo_root, "rev-parse", "HEAD").strip()
    message = _git(repo_root, "log", "-1", "--pretty=%B", commit_sha)
    files_raw = _git(repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    files = [f for f in files_raw.splitlines() if f]
    return handle_post_commit(conn, commit_sha=commit_sha, message=message, files=files)
