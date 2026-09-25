"""Drift loop (plan section 9, P7): anchor tracking, post-commit
staleness marking, unattributed-commit inbox signals, `audit`, the real
`drift_pct` KPI, and lesson decay.

"Anchors: file path + content hash with git rename detection.
Symbol-level anchors via tree-sitter deferred" (plan section 9). P6 left
`components.anchors_json` at its schema default (`'[]'`) -- P7 is the
first phase that actually writes to it, as a `{file_path: content_hash}`
dict (not the `'[]'` list the column defaults to; a component with no
anchors yet keeps the default).

Every mutation here goes through this module's own functions, called
from `core.hooks`/`core.review`/CLI/API -- never raw SQL from those
callers (working rule 3), same pattern `core.close`/`core.risk` already
established for the structure-graph tables they own.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import events as events_mod

# "K commits" for the drift KPI (plan section 9, item 5: "% components
# verified within last K commits"). No sizing guidance in the plan;
# 50 keeps the window roughly "recent project history" without needing
# per-repo tuning -- see docs/decisions.md. Module attribute (not a
# local constant) so a caller/test can override it, same pattern as
# `core.close.MAX_DIFF_ITEMS`.
DEFAULT_DRIFT_WINDOW_COMMITS = 50

# "not retrieved in K projects" (plan section 9, lesson decay). Same
# reasoning: no sizing guidance, 3 is a sensible default that doesn't
# archive a lesson after a single unlucky project -- see docs/decisions.md.
DEFAULT_LESSON_DECAY_K = 3

# `audit` (plan section 9, item 4) samples this many oldest-verified
# components per run, same "capped" framing `core.close.MAX_DIFF_ITEMS`
# already uses for diff-sized review batches.
DEFAULT_AUDIT_SAMPLE = 5


def _now(conn: sqlite3.Connection) -> str:
    return db_mod.query_one(conn, "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")[0]


def _git(repo_root: str | Path, *args: str) -> subprocess.CompletedProcess:
    """Best-effort git call: returns a `CompletedProcess` with a non-zero
    `returncode` (never raises) when `repo_root` isn't a git repo at all
    or git isn't on PATH -- callers (e.g. the KPI endpoint, which may run
    against a repo without history yet) degrade to "no signal" instead of
    500ing."""
    try:
        return subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True, text=True,
        )
    except OSError as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def blob_hash(repo_root: str | Path, sha: str, path: str) -> str | None:
    """Content hash of `path` as of commit `sha`, or None if it doesn't
    exist there (deleted, or never existed on that commit)."""
    result = _git(repo_root, "show", f"{sha}:{path}")
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout.encode()).hexdigest()


def _load_anchors(row: sqlite3.Row) -> dict:
    try:
        anchors = json.loads(row["anchors_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return anchors if isinstance(anchors, dict) else {}


def create_anchored_component(
    conn: sqlite3.Connection,
    *,
    name: str,
    file_path: str,
    repo_root: str | Path,
    kind: str | None = None,
    purpose: str | None = None,
    actor: str = "human",
) -> dict:
    """Create a component anchored to a real file at HEAD, hashing its
    current content into `anchors_json`. This is the concrete-path
    counterpart to `core.close`'s glob-based `_component_candidates`:
    once a candidate names a real file (not a glob), it gets a real
    anchor hash instead of the empty `'[]'` `close` still writes for
    glob-named candidates (symbol-level/multi-file anchors stay out of
    scope -- explicitly deferred, plan section 9)."""
    head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    content_hash = blob_hash(repo_root, head, file_path) if head else None
    anchors = {file_path: content_hash}
    with db_mod.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO components (name, kind, purpose, anchors_json, status, verified_sha) "
            "VALUES (?, ?, ?, ?, 'current', ?)",
            (name, kind, purpose, json.dumps(anchors), head or None),
        )
        row = conn.execute("SELECT * FROM components WHERE id = ?", (cur.lastrowid,)).fetchone()
        events_mod.record_event(
            conn, project_id=None, node_id=None, actor=actor, actor_evidence="tty",
            type_="component.created", payload=dict(row),
        )
        return dict(row)


def _rename_map(repo_root: str | Path, sha: str) -> dict[str, str]:
    """`old_path -> new_path` for every rename `git` detects in `sha`
    (plan section 9: "git rename detection"), via `diff-tree -M` --
    real git rename-detection semantics, not a heuristic re-implemented
    here."""
    result = _git(repo_root, "diff-tree", "-M", "--no-commit-id", "--name-status", "-r", sha)
    renames: dict[str, str] = {}
    if result.returncode != 0:
        return renames
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[1]] = parts[2]
    return renames


def mark_stale_for_commit(
    conn: sqlite3.Connection,
    repo_root: str | Path,
    commit_sha: str,
    files: list[str],
    *,
    actor: str = "hook",
) -> list[int]:
    """Drift loop item 1 (plan section 9): for every non-deprecated
    component whose `anchors_json` names a file this commit touched,
    recompute its content hash. A pure rename (detected via
    `_rename_map`) retargets the anchor to the new path without marking
    the component stale. Any other content change flips
    `components.status` to `'stale'` and records a `component.updated`
    event carrying the full row (rebuild-replayable, same convention
    `nodes`/`projects` use). Returns the ids newly marked stale."""
    renames = _rename_map(repo_root, commit_sha)
    touched = set(files)
    newly_stale: list[int] = []
    with db_mod.write_txn(conn):
        rows = conn.execute("SELECT * FROM components WHERE status != 'deprecated'").fetchall()
        for row in rows:
            anchors = _load_anchors(row)
            if not anchors:
                continue
            new_anchors = dict(anchors)
            changed = False
            stale = False
            for path, old_hash in anchors.items():
                if path in renames:
                    new_path = renames[path]
                    new_anchors.pop(path, None)
                    new_anchors[new_path] = blob_hash(repo_root, commit_sha, new_path)
                    changed = True
                    continue
                if path not in touched:
                    continue
                new_hash = blob_hash(repo_root, commit_sha, path)
                if new_hash != old_hash:
                    new_anchors[path] = new_hash
                    changed = True
                    stale = True
            if not changed:
                continue
            new_status = "stale" if stale else row["status"]
            conn.execute(
                "UPDATE components SET anchors_json = ?, status = ? WHERE id = ?",
                (json.dumps(new_anchors), new_status, row["id"]),
            )
            updated = conn.execute("SELECT * FROM components WHERE id = ?", (row["id"],)).fetchone()
            events_mod.record_event(
                conn, project_id=None, node_id=None, actor=actor, actor_evidence="hook",
                type_="component.updated", payload=dict(updated),
            )
            if stale:
                newly_stale.append(row["id"])
    return newly_stale


def flag_unattributed_commit(
    conn: sqlite3.Connection,
    *,
    commit_sha: str,
    files: list[str],
    node_ids: list[int],
    actor: str = "hook",
) -> int | None:
    """Drift loop item 2 (plan section 9): a commit with no
    `Muvue-Node:`/`Refs:` trailer (`node_ids` empty -- see
    `core.trailers.parse_node_ids`) that touches a file any tracked
    component is anchored to creates an unacked `inbox.unattributed_commit`
    event (P2's inbox/events machinery: unacked events are what the
    dashboard's inbox already surfaces, same convention `/events/{id}/ack`
    uses for every other inbox-shaped event -- see docs/decisions.md).
    Returns the new event id, or None if nothing was flagged."""
    if node_ids or not files:
        return None
    touched = set(files)
    rows = db_mod.query_all(
        conn,
        "SELECT id, anchors_json FROM components WHERE status != 'deprecated'",
    )
    hit_components = [
        row["id"] for row in rows if touched & set(_load_anchors(row).keys())
    ]
    if not hit_components:
        return None
    return events_mod.record_event(
        conn, project_id=None, node_id=None, actor=actor, actor_evidence="hook",
        type_="inbox.unattributed_commit",
        payload={"sha": commit_sha, "files": sorted(touched), "component_ids": hit_components},
    )


def flag_general_unattributed_commit(
    conn: sqlite3.Connection,
    *,
    commit_sha: str,
    files: list[str],
    node_ids: list[int],
    resolved_node_ids: list[int],
    actor: str = "hook",
) -> int | None:
    """v4 section 7 / changelog item 10: commit-trailer enforcement moves
    from `PreToolUse` string-matching (removed -- see
    `core.claude_hooks`/`muvue._hook`) to post-hoc `post-commit`
    detection. Broader than `flag_unattributed_commit` above (P7 drift
    loop item 2, kept unchanged, additive not replaced -- see
    docs/decisions.md #100): fires for *every* commit whose trailer is
    missing entirely (`node_ids` empty) or present but doesn't resolve to
    any real, non-deleted node (`resolved_node_ids` empty despite
    `node_ids` not being), regardless of whether it touched a file any
    tracked structure component happens to be anchored to.

    Decision (docs/decisions.md #100): scope is unconditional -- not
    limited to commits that touch a currently-`in_progress` node's
    touches, and not gated on any node being leased at all. The plan's
    own wording notes the old `PreToolUse`-scoped mechanism had a
    tool-call context this post-hoc replacement doesn't have available,
    and offers no single canonical narrower scoping rule; the simplest
    reading, and the one that errs toward "detection everywhere else"
    (plan principle 10) rather than silently missing real unattributed
    work, is to flag every commit whose trailer doesn't resolve.

    Records one unacked `unattributed_commit` event -- a distinct type
    from `inbox.unattributed_commit` (P7's anchored-component-only
    signal, which keeps its own name and its own narrower firing
    condition). `GET /inbox` surfaces unacked `unattributed_commit`
    events under `"unattributed_commits"`. Returns the new event id, or
    None if this commit's trailer resolved to at least one real node."""
    if resolved_node_ids:
        return None
    return events_mod.record_event(
        conn, project_id=None, node_id=None, actor=actor, actor_evidence="hook",
        type_="unattributed_commit",
        payload={
            "sha": commit_sha,
            "files": sorted(set(files)),
            "trailer_node_ids": node_ids,
        },
    )


def run_audit(
    conn: sqlite3.Connection,
    *,
    n: int = DEFAULT_AUDIT_SAMPLE,
    lesson_decay_k: int = DEFAULT_LESSON_DECAY_K,
    actor: str = "agent",
) -> dict:
    """Drift loop item 4 (plan section 9): sample the `n` oldest-verified
    (or never-verified) non-deprecated components and draft a proposed
    diff for each into the inbox (unacked `inbox.audit_drift_signal`
    events). `components` carries no creation timestamp (P0 schema), so
    "oldest" is read as: never-verified first (maximally overdue), then
    ascending `id` as a proxy for insertion order -- see
    docs/decisions.md.

    Also runs a lesson-decay pass (plan section 9's "Lesson decay"
    paragraph: "`audit` may prune") -- `decay_lessons` archives any
    lesson not retrieved by `lesson_decay_k` distinct projects, unless
    pinned.
    """
    with db_mod.write_txn(conn):
        rows = conn.execute(
            "SELECT * FROM components WHERE status != 'deprecated' "
            "ORDER BY (verified_sha IS NULL) DESC, id ASC LIMIT ?",
            (n,),
        ).fetchall()
        drafted = []
        for row in rows:
            message = (
                f"component {row['id']} ({row['name']}) last verified at "
                f"{row['verified_sha'] or 'never'}; sampled by audit for review."
            )
            event_id = events_mod.record_event(
                conn, project_id=None, node_id=None, actor=actor, actor_evidence="subprocess",
                type_="inbox.audit_drift_signal",
                payload={
                    "component_id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "verified_sha": row["verified_sha"],
                    "message": message,
                },
            )
            drafted.append({"event_id": event_id, "component_id": row["id"], "message": message})

        pruned = decay_lessons(conn, k=lesson_decay_k, actor=actor)
        return {"drafted": drafted, "pruned_lessons": pruned}


def drift_pct(
    conn: sqlite3.Connection, repo_root: str | Path, *, k: int = DEFAULT_DRIFT_WINDOW_COMMITS
) -> float:
    """Drift loop item 5 (plan section 9): "% components verified within
    last K commits" -- fraction of non-deprecated components whose
    `verified_sha` is among `main`'s (HEAD's) last `k` commits. 0.0 when
    there are no non-deprecated components, or `repo_root` has no usable
    git history (fresh/non-git repo -- see `_git`)."""
    components = db_mod.query_all(
        conn,
        "SELECT verified_sha FROM components WHERE status != 'deprecated'",
    )
    if not components:
        return 0.0
    result = _git(repo_root, "log", f"-n{k}", "--format=%H", "HEAD")
    recent = set(result.stdout.split()) if result.returncode == 0 else set()
    if not recent:
        return 0.0
    verified_recent = sum(1 for c in components if c["verified_sha"] in recent)
    return verified_recent / len(components)


def record_lesson_retrieval(
    conn: sqlite3.Connection, note_id: int, project_id: int | None, *, actor: str = "agent"
) -> None:
    """Called from `core.queries.brief_node` each time a lesson note is
    surfaced (plan section 9: "not retrieved in K projects" needs a
    retrieval signal to count against). Updates
    `notes.last_retrieved_at` and appends a `note.retrieved` event --
    `decay_lessons` counts distinct `project_id`s from these events
    rather than a separate junction table (see docs/decisions.md)."""
    with db_mod.write_txn(conn):
        conn.execute(
            "UPDATE notes SET last_retrieved_at = ? WHERE id = ?",
            (_now(conn), note_id),
        )
        events_mod.record_event(
            conn, project_id=project_id, node_id=None, actor=actor, actor_evidence="subprocess",
            type_="note.retrieved", payload={"note_id": note_id, "project_id": project_id},
        )


def decay_lessons(
    conn: sqlite3.Connection, *, k: int = DEFAULT_LESSON_DECAY_K, actor: str = "agent"
) -> list[int]:
    """"Lesson decay: not retrieved in K projects -> archived unless
    pinned" (plan section 9). A lesson note (`kind='lesson'`, not
    `pinned`, not already `archived_at`) whose `note.retrieved` events
    (see `record_lesson_retrieval`) span fewer than `k` distinct
    `project_id`s is archived (`archived_at` set, soft-delete convention
    -- `notes` has no `deleted_at`, so a dedicated column is added
    instead of overloading one, see docs/decisions.md), recorded as a
    `note.archived` event. Returns the archived note ids."""
    with db_mod.write_txn(conn):
        candidates = conn.execute(
            "SELECT * FROM notes WHERE kind = 'lesson' AND pinned = 0 AND archived_at IS NULL"
        ).fetchall()
        archived: list[int] = []
        for note in candidates:
            distinct_projects = conn.execute(
                "SELECT COUNT(DISTINCT json_extract(payload, '$.project_id')) c "
                "FROM events WHERE type = 'note.retrieved' "
                "AND json_extract(payload, '$.note_id') = ?",
                (note["id"],),
            ).fetchone()["c"]
            if distinct_projects >= k:
                continue
            conn.execute(
                "UPDATE notes SET archived_at = ? WHERE id = ?", (_now(conn), note["id"])
            )
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note["id"],)).fetchone()
            events_mod.record_event(
                conn, project_id=None, node_id=row["node_id"], actor=actor, actor_evidence="subprocess",
                type_="note.archived", payload=dict(row),
            )
            archived.append(note["id"])
        return archived
