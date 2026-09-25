"""Git hook business logic invoked by the shims `repo_init.py` installs
(plan section 5/7): `muvue hook post-commit` parses `Muvue-Node:`/`Refs:`
trailers (core.trailers), links the commit to any resolvable node in
`node_commits`, and (P7, drift loop items 1-2, plan section 9) flags an
unattributed commit that touches an anchored file straight to the
inbox. Real anchor-hash recompute/staleness marking (drift loop item 1)
needs actual git access to hash blobs at the commit, so it lives in
`handle_post_commit_from_git`/`handle_post_commit_from_git_sha` (below)
rather than here -- see those functions' docstrings and
`core.drift.mark_stale_for_commit`. P3 shipped this hook enqueueing
no-op `anchor.hash_requested`/`staleness.flagged` signals for a future
consumer to drain; those events are still recorded (nothing here
removes a previously-shipped event type) but P7 makes the staleness
marking they described real, not stubbed.

Every mutation here goes through `muvue.core` only (working rule 3): this
module issues its own SQL for `node_commits` (a table no other module
owns yet) but always through `record_event`/`conn` the same way
`nodes.py` does, never bypassing the events log.

v4 section 4a (P0.5, hook fast path): `drain_queue` (bottom of this
file) is the *other* end of `muvue._hook`'s `.muvue/queue.jsonl` spool.
`muvue._hook` itself never imports this module (or anything else under
`muvue.core` -- see its own docstring); this module is the normal
`core` code the fast path's spooled lines get processed through later,
by a plain CLI invocation or the daemon, both of which already pay for
importing `muvue.core` anyway.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from . import db as db_mod
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
    with db_mod.write_txn(conn):
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
            # v4 section 3: "actual_touches -- written from commits; drift
            # vs predicted is a KPI." One row per real file path this
            # commit touched, linked to the node the trailer resolved --
            # the KPI computation itself (drift vs predicted_touches) is
            # P6/P7-scoped future work; this just gets the raw data in.
            for path in files or []:
                conn.execute(
                    "INSERT OR IGNORE INTO actual_touches (node_id, path) VALUES (?, ?)",
                    (node_id, path),
                )
            events_mod.record_event(
                conn,
                project_id=node["project_id"],
                node_id=node_id,
                actor="hook",
                actor_evidence="hook",
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
                conn, project_id=None, node_id=None, actor="hook", actor_evidence="hook",
                type_="anchor.hash_requested",
                payload={"sha": commit_sha, "node_ids": linked},
            )
            events_mod.record_event(
                conn, project_id=None, node_id=None, actor="hook", actor_evidence="hook",
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
        # v4 section 7 / changelog item 10 (relocation of the removed
        # PreToolUse git-commit-trailer block, now post-hoc): the general
        # case, unconditional on components existing or being touched --
        # see core.drift.flag_general_unattributed_commit's docstring and
        # docs/decisions.md #100.
        drift_mod.flag_general_unattributed_commit(
            conn, commit_sha=commit_sha, files=files or [], node_ids=node_ids,
            resolved_node_ids=linked,
        )
        unresolved = [n for n in node_ids if n not in linked]
        return {"commit_sha": commit_sha, "linked_node_ids": linked, "unresolved_ids": unresolved}


def _git(repo_root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True,
    )
    return out.stdout


def handle_post_commit_from_git(conn: sqlite3.Connection, repo_root: Path) -> dict:
    """Real entry point for the installed (pre-v4) full-CLI shim /
    `muvue hook post-commit`: resolve HEAD's sha and delegate to
    `handle_post_commit_from_git_sha`."""
    commit_sha = _git(repo_root, "rev-parse", "HEAD").strip()
    return handle_post_commit_from_git_sha(conn, repo_root, commit_sha)


def handle_post_commit_from_git_sha(
    conn: sqlite3.Connection, repo_root: Path, commit_sha: str
) -> dict:
    """Same as `handle_post_commit_from_git`, parameterized on an
    explicit `commit_sha` instead of always reading HEAD -- what
    `drain_queue` (v4 section 4a) needs, since a spooled `post-commit`
    line only carries the sha the fast path captured at commit time
    (v4 section 4a: "everything else about that commit's diff can be
    re-derived from git when the queue is drained"), and by drain time
    HEAD may have moved past it (several commits queued back to back).
    Reads HEAD's/`commit_sha`'s message/touched files from git,
    delegates to `handle_post_commit` (trailer parsing, `node_commits`
    linking, unattributed-commit inbox flag), then (P7, drift loop item
    1) recompute anchor hashes and mark stale every component whose
    anchored file this commit changed (`core.drift.
    mark_stale_for_commit`) -- the real git blob access
    `handle_post_commit` itself deliberately doesn't have, "within one
    commit" of the hand-edit landing (plan P7 acceptance #1)."""
    message = _git(repo_root, "log", "-1", "--pretty=%B", commit_sha)
    files_raw = _git(repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    files = [f for f in files_raw.splitlines() if f]
    result = handle_post_commit(conn, commit_sha=commit_sha, message=message, files=files)
    newly_stale = drift_mod.mark_stale_for_commit(conn, repo_root, commit_sha, files)
    result["newly_stale_component_ids"] = newly_stale
    return result


# --------------------------------------------------------------------------
# v4 section 4a: bounded queue drain, the other end of `muvue._hook`'s
# `.muvue/queue.jsonl` spool. "The daemon drains continuously; absent a
# daemon, the next CLI call drains at most 200 items or 200 ms,
# whichever comes first, then leaves the rest."
# --------------------------------------------------------------------------

QUEUE_RELPATH = ".muvue/queue.jsonl"
DRAINING_RELPATH = ".muvue/queue.draining"
LOCK_RELPATH = ".muvue/queue.lock"
DEFAULT_DRAIN_MAX_ITEMS = 200
DEFAULT_DRAIN_MAX_SECONDS = 0.2


def _drain_clock() -> float:
    """Indirection point so tests can inject a deterministic clock
    (`monkeypatch.setattr(hooks, "_drain_clock", fake_clock)`) instead
    of racing a real 200ms wall-clock bound."""
    return time.monotonic()


def _process_queue_event(conn: sqlite3.Connection, repo_root: Path, event: dict) -> None:
    """Dispatch one spooled line to the existing full handler for its
    event type (working rule: reuse, don't reimplement). `post-commit`
    is the only spooled event with real DB mutation work left to do (the
    Claude Code decision hooks decide synchronously in `muvue._hook.run`;
    nothing about them is a deferred *write*); the rest are
    recorded as an audit-trail `hook.<event>` event (v4 principle 10:
    "detection everywhere else") so a fast-path event that COULD have
    mattered (e.g. a `hook_timeout`) stays visible even though nothing
    else in `core` currently consumes it."""
    etype = event.get("event")
    if etype == "post-commit":
        sha = event.get("sha")
        if sha:
            handle_post_commit_from_git_sha(conn, repo_root, sha)
        return

    node_id = event.get("node_id")
    project_id = None
    if node_id is not None:
        # Boundary validation: the queue file is untrusted (a hand-edit
        # or a stale node_id from a since-deleted node) -- don't pass a
        # dangling node_id into a FK'd column.
        row = db_mod.query_one(
            conn,
            "SELECT project_id FROM nodes WHERE id = ? AND deleted_at IS NULL",
            (node_id,),
        )
        if row is None:
            node_id = None
        else:
            project_id = row["project_id"]
    with db_mod.write_txn(conn):
        events_mod.record_event(
            conn,
            project_id=project_id,
            node_id=node_id,
            actor="hook",
            actor_evidence="hook",
            type_=f"hook.{etype}",
            payload=event,
        )


def drain_queue(
    conn: sqlite3.Connection,
    repo_root: Path,
    *,
    max_items: int = DEFAULT_DRAIN_MAX_ITEMS,
    max_seconds: float = DEFAULT_DRAIN_MAX_SECONDS,
) -> dict:
    """Process at most `max_items` spooled lines, or stop early once
    `max_seconds` of wall-clock time has elapsed, whichever comes first
    (v4 section 4a) -- leaving the rest for the next call (or the
    daemon's continuous loop), oldest first. A second drainer that finds
    the lock held backs off and drains nothing. A malformed line (not valid JSON)
    still counts against `max_items` and is dropped rather than
    retried forever. Returns `{"drained": n, "remaining": n}`."""
    repo_root = Path(repo_root)
    queue_path = repo_root / QUEUE_RELPATH
    draining_path = repo_root / DRAINING_RELPATH
    if not queue_path.exists() and not draining_path.exists():
        return {"drained": 0, "remaining": 0}

    # Hand-off protocol: hooks only ever append to `queue.jsonl`. The
    # drainer atomically renames it to `queue.draining` (a hook append
    # that starts after the rename creates a fresh `queue.jsonl`), and
    # only the lock holder ever reads or rewrites `queue.draining`. The
    # old read-process-overwrite of `queue.jsonl` itself dropped any line
    # appended while processing ran.
    lock_path = repo_root / LOCK_RELPATH
    with lock_path.open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"drained": 0, "remaining": _pending_line_count(repo_root)}
        try:
            start = _drain_clock()
            processed = 0
            while True:
                if not draining_path.exists():
                    if not queue_path.exists():
                        break
                    os.replace(queue_path, draining_path)
                raw = draining_path.read_bytes()
                lines = raw.decode().splitlines()
                cursor = len(lines)
                for i, line in enumerate(lines):
                    if processed >= max_items or (_drain_clock() - start) > max_seconds:
                        cursor = i
                        break
                    processed += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _process_queue_event(conn, repo_root, event)

                remainder = lines[cursor:]
                # A hook that opened `queue.jsonl` just before the rename
                # may still have landed its line in the renamed file after
                # we read it -- carry those bytes over, never discard them.
                remainder += draining_path.read_bytes()[len(raw):].decode().splitlines()
                if remainder:
                    draining_path.write_text("".join(f"{ln}\n" for ln in remainder))
                    break
                draining_path.unlink()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    return {"drained": processed, "remaining": _pending_line_count(repo_root)}


def _pending_line_count(repo_root: Path) -> int:
    total = 0
    for rel in (DRAINING_RELPATH, QUEUE_RELPATH):
        path = repo_root / rel
        if path.exists():
            total += sum(1 for line in path.read_text().splitlines() if line.strip())
    return total
