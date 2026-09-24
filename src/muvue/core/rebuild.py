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


def rebuild_state(conn: sqlite3.Connection) -> dict:
    """Replay `events` and return {"projects": {id: row_dict}, "nodes": {...}}."""
    projects: dict[int, dict] = {}
    nodes: dict[int, dict] = {}
    for ev in conn.execute("SELECT * FROM events ORDER BY id ASC"):
        payload = json.loads(ev["payload"])
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
        # note.* events don't affect projects/nodes replay state.
    return {"projects": projects, "nodes": nodes}


def live_state(conn: sqlite3.Connection) -> dict:
    projects = {
        row["id"]: {c: row[c] for c in _PROJECT_COLUMNS}
        for row in conn.execute("SELECT * FROM projects")
    }
    nodes = {
        row["id"]: {c: row[c] for c in _NODE_COLUMNS}
        for row in conn.execute("SELECT * FROM nodes")
    }
    return {"projects": projects, "nodes": nodes}


def diff_state(conn: sqlite3.Connection) -> dict:
    """Return {} if replay-from-events equals the live DB, else a dict of
    mismatches for debugging."""
    replayed = rebuild_state(conn)
    live = live_state(conn)
    mismatches: dict = {}
    for table in ("projects", "nodes"):
        cols = _PROJECT_COLUMNS if table == "projects" else _NODE_COLUMNS
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
