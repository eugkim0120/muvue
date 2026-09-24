"""Rebuild live state by replaying `events` only.

Every mutation in nodes.py/projects.py appends an event whose payload is a
full snapshot of the row after the mutation. Replay is therefore
last-write-wins per (table, id): folding the event stream in id order
reproduces the live `projects` and `nodes` tables' *replayable* columns
exactly.

v4 section 3 ("Events, replay, and what replay does *not* cover")
narrows what "replay equals live DB" actually means -- v3's P0
acceptance as originally written ("replay equals live DB") was false: it
included clock- and read-derived fields that events can never
reproduce, because they aren't decisions the system made, they're
observations of the outside world (wall-clock time, "was this read
recently").

- **Replayable** (the P0 acceptance applies): node existence, status,
  parent/dep edges, criteria + criteria_hash, owner, attempts,
  `lease_expiries` (the *consequence* of a lease clock expiring is
  replayable even though the clock itself is not -- v4 section 3), notes,
  commits, approvals, spend.
- **Not replayable, excluded from the equality check:** `lease_until`
  (wall-clock -- it's set to "now + lease_minutes" at `start` time, and a
  replay run happening at a different wall-clock moment than the
  original `start` call cannot reproduce the same absolute timestamp),
  `last_retrieved_at` (read-side telemetry -- set by `core.queries.
  brief_node`/`core.drift.record_lesson_retrieval` merely *reading* a
  lesson, not a decision the write path made), FTS5 index contents (a
  derived search index, not project state), `verified_sha` freshness
  (whether a component's `verified_sha` reflects a *recent* audit is a
  time-relative judgment `core.drift.drift_pct` makes against the
  outside world's current git history, not something replay owns --
  the `verified_sha` *value* itself, set once by an event payload, is
  still compared normally).

`_REPLAYABLE_NODE_COLUMNS`/`_REPLAYABLE_NOTE_COLUMNS` below are the
column lists `diff_state`/`diff_project_from_archive` actually compare;
`_NODE_COLUMNS`/`_NOTE_COLUMNS` (the full row shape) still exist for
`live_state`'s general-purpose snapshot (used elsewhere, e.g. the
history export's replay target), just not for the equality check.
"""

from __future__ import annotations

import json
import sqlite3

_PROJECT_COLUMNS = [
    "id", "goal", "phase", "budget_unit", "budget_limit", "spent", "created_at", "closed_at",
    "branch",
]
_NODE_COLUMNS = [
    "id", "project_id", "parent_id", "kind", "title", "body_md", "status",
    "block_reason", "criteria_json", "criteria_hash", "criteria_mode",
    "risk_tier", "version", "owner", "lease_until", "attempts", "lease_expiries",
    "max_attempts", "summary", "worktree", "deleted_at", "created_at",
]
# P7: `core.drift.mark_stale_for_commit`/`decay_lessons` are the first
# mutations against `components`/`notes` (plan section 9's drift loop
# and lesson decay), so those two tables join `projects`/`nodes` in the
# replay/diff coverage below -- every new mutating flow gets a rebuild
# test (working rule, see docs/decisions.md).
_COMPONENT_COLUMNS = ["id", "name", "kind", "purpose", "anchors_json", "status", "verified_sha"]
_NOTE_COLUMNS = [
    "id", "node_id", "kind", "text", "content_hash", "pinned",
    "last_retrieved_at", "archived_at", "created_at",
]

# v4 section 3: the columns actually compared by `diff_state`/
# `diff_project_from_archive` -- `lease_until` and `last_retrieved_at`
# excluded per the module docstring above. `projects`/`components` have
# no non-replayable columns of their own this phase (a project's
# `created_at`/`closed_at` are set once, from an event payload, exactly
# like any other column -- not re-derived from "now" on each replay --
# so they stay replayable; `verified_sha`'s *value* likewise stays in,
# only its real-time "freshness" judgment, computed elsewhere in
# `core.drift.drift_pct`, is out of scope for this equality check).
_REPLAYABLE_PROJECT_COLUMNS = list(_PROJECT_COLUMNS)
_REPLAYABLE_NODE_COLUMNS = [c for c in _NODE_COLUMNS if c != "lease_until"]
_REPLAYABLE_COMPONENT_COLUMNS = list(_COMPONENT_COLUMNS)
_REPLAYABLE_NOTE_COLUMNS = [c for c in _NOTE_COLUMNS if c != "last_retrieved_at"]


def rebuild_state_from_events(events: list[dict]) -> dict:
    """Fold an arbitrary in-order sequence of event dicts (each with at
    least `type`/`payload`, `payload` a JSON string as stored in the
    `events` table) into {"projects": {...}, "nodes": {...}}. Shared by
    `rebuild_state` (the full live `events` table) and
    `core.history.rebuild_from_archive` (P6: a single project's exported
    `.jsonl.gz` archive) -- both are just different sources of the same
    event-dict shape, so the fold logic only needs to exist once (plan
    working rule: single write/replay path)."""
    projects: dict[int, dict] = {}
    nodes: dict[int, dict] = {}
    components: dict[int, dict] = {}
    notes: dict[int, dict] = {}
    for ev in events:
        payload = ev["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        etype = ev["type"]
        if etype.startswith("project."):
            projects[payload["id"]] = payload
        elif etype.startswith("node."):
            # Most `node.*` events carry the full row as the payload
            # itself. `node.criteria_edited` (core.gates.edit_criteria,
            # P1) is the one exception: its payload is
            # {"node": <row>, "re_approval_required": bool}, so the row
            # snapshot is nested. Unwrap it the same way here rather than
            # special-casing the event type, so any future node.* event
            # that nests its snapshot under "node" replays correctly too.
            snapshot = payload["node"] if "node" in payload and "id" not in payload else payload
            nodes[snapshot["id"]] = snapshot
        elif etype in ("component.created", "component.updated"):
            # Full-row-snapshot events (P6/P7). `component.staleness_marked`-
            # shaped signals don't exist; staleness is folded into the
            # row itself via `component.updated` (core.drift), same
            # last-write-wins convention as `node.*`.
            components[payload["id"]] = payload
        elif etype in ("note.added", "note.archived"):
            # Full-row-snapshot events too. `note.retrieved` (P7 lesson
            # retrieval tracking, core.drift.record_lesson_retrieval) is
            # deliberately excluded: its payload is
            # {"note_id", "project_id"}, not a row snapshot -- it drives
            # `decay_lessons`' distinct-project count, not `notes` replay.
            notes[payload["id"]] = payload
        # decision.*/external_ref.* events don't affect this replay state.
    return {"projects": projects, "nodes": nodes, "components": components, "notes": notes}


def rebuild_state(conn: sqlite3.Connection) -> dict:
    """Replay `events` and return {"projects": {id: row_dict}, "nodes": {...}}."""
    events = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id ASC")]
    return rebuild_state_from_events(events)


def live_state(conn: sqlite3.Connection) -> dict:
    projects = {
        row["id"]: {c: row[c] for c in _PROJECT_COLUMNS}
        for row in conn.execute("SELECT * FROM projects")
    }
    nodes = {
        row["id"]: {c: row[c] for c in _NODE_COLUMNS}
        for row in conn.execute("SELECT * FROM nodes")
    }
    components = {
        row["id"]: {c: row[c] for c in _COMPONENT_COLUMNS}
        for row in conn.execute("SELECT * FROM components")
    }
    notes = {
        row["id"]: {c: row[c] for c in _NOTE_COLUMNS}
        for row in conn.execute("SELECT * FROM notes")
    }
    return {"projects": projects, "nodes": nodes, "components": components, "notes": notes}


_TABLE_COLUMNS = {
    "projects": _REPLAYABLE_PROJECT_COLUMNS,
    "nodes": _REPLAYABLE_NODE_COLUMNS,
    "components": _REPLAYABLE_COMPONENT_COLUMNS,
    "notes": _REPLAYABLE_NOTE_COLUMNS,
}


def _diff_tables(replayed: dict, live: dict, tables: tuple[str, ...] = ("projects", "nodes")) -> dict:
    """Compare only the **replayable projection** (v4 section 3) of each
    table -- `_TABLE_COLUMNS` above already excludes `lease_until`/
    `last_retrieved_at`, so a live/replayed difference confined to those
    columns alone never shows up as a mismatch here."""
    mismatches: dict = {}
    for table in tables:
        cols = _TABLE_COLUMNS[table]
        r_table, l_table = replayed[table], live[table]
        if set(r_table) != set(l_table):
            mismatches[table] = {"id_mismatch": (set(r_table), set(l_table))}
            continue
        for id_, live_row_full in l_table.items():
            # `live_row_full` comes from `live_state`, which snapshots the
            # *full* row shape (all of `_NODE_COLUMNS`, including
            # `lease_until`) -- project it down to the replayable columns
            # here too, so a live/replayed difference confined to a
            # non-replayable column (e.g. real wall-clock time having
            # passed between `live_state()` and `rebuild_state()`) is
            # never compared at all, not just tolerated.
            live_row = {c: live_row_full.get(c) for c in cols}
            replayed_row = {c: r_table[id_].get(c) for c in cols}
            if replayed_row != live_row:
                mismatches.setdefault(table, {})[id_] = {
                    "live": live_row,
                    "replayed": replayed_row,
                }
    return mismatches


def diff_state(conn: sqlite3.Connection) -> dict:
    """Return {} if replay-from-events equals the live DB, else a dict of
    mismatches for debugging. Covers `components`/`notes` (P7) alongside
    `projects`/`nodes` (P0)."""
    replayed = rebuild_state(conn)
    live = live_state(conn)
    return _diff_tables(replayed, live, tables=("projects", "nodes", "components", "notes"))


def diff_project_from_archive(conn: sqlite3.Connection, project_id: int, archive_path) -> dict:
    """P6 acceptance #3, project-scoped variant of `diff_state`: return {}
    if replaying *only* `project_id`'s exported `.jsonl.gz` archive
    (`core.history.export_project`) reproduces that project's row and its
    nodes exactly as they are live right now, else a dict of mismatches.
    Live state is filtered down to this one project so a project that
    happens to share an id-space with others in the same DB doesn't
    trip an unrelated "id_mismatch"."""
    from . import history as history_mod  # local import: avoids a cycle (history -> rebuild)

    replayed = history_mod.rebuild_from_archive(archive_path)
    live_full = live_state(conn)
    live = {
        "projects": {
            id_: row for id_, row in live_full["projects"].items() if id_ == project_id
        },
        "nodes": {
            id_: row for id_, row in live_full["nodes"].items() if row["project_id"] == project_id
        },
    }
    return _diff_tables(replayed, live)
