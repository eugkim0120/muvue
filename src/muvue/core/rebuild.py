"""Rebuild live state by replaying `events` only (plan P0 acceptance #2).

Every mutation in nodes.py/projects.py appends an event whose payload is a
full snapshot of the row after the mutation. Replay is therefore
last-write-wins per (table, id): folding the event stream in id order
reproduces the live `projects` and `nodes` tables exactly.
"""

from __future__ import annotations

import json
import sqlite3

_PROJECT_COLUMNS = [
    "id", "goal", "phase", "budget_unit", "budget_limit", "spent", "created_at",
]
_NODE_COLUMNS = [
    "id", "project_id", "parent_id", "kind", "title", "body_md", "status",
    "block_reason", "criteria_json", "criteria_hash", "criteria_mode",
    "risk_tier", "version", "owner", "lease_until", "attempts", "max_attempts",
    "summary", "worktree", "deleted_at", "created_at",
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
    "projects": _PROJECT_COLUMNS,
    "nodes": _NODE_COLUMNS,
    "components": _COMPONENT_COLUMNS,
    "notes": _NOTE_COLUMNS,
}


def _diff_tables(replayed: dict, live: dict, tables: tuple[str, ...] = ("projects", "nodes")) -> dict:
    mismatches: dict = {}
    for table in tables:
        cols = _TABLE_COLUMNS[table]
        r_table, l_table = replayed[table], live[table]
        if set(r_table) != set(l_table):
            mismatches[table] = {"id_mismatch": (set(r_table), set(l_table))}
            continue
        for id_, live_row in l_table.items():
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
