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

Beyond the row-snapshot tables (`projects`, `nodes`, `components`,
`notes`), replay folds the keyed relations the section 3 list names:
`deps` (`dep.added`), `node_commits` and `actual_touches`
(`commit.linked`), plan-revision approvals (`revision.proposed`/
`revision.approved`, compared as approved-or-not since `approved_at` is a
wall-clock value) and `agent_spend` (summed `spend.recorded`).

`apply_rebuild` writes the replayed set back over the live tables
(`muvue rebuild --apply`) and re-derives the FTS indexes; tables outside
the replayable set (questions, predicted touches, usage, ...) are left
as they are.

`_REPLAYABLE_NODE_COLUMNS`/`_REPLAYABLE_NOTE_COLUMNS` below are the
column lists `diff_state`/`diff_project_from_archive` actually compare;
`_NODE_COLUMNS`/`_NOTE_COLUMNS` (the full row shape) still exist for
`live_state`'s general-purpose snapshot (used elsewhere, e.g. the
history export's replay target), just not for the equality check.
"""

from __future__ import annotations

import json
import sqlite3

from . import db as db_mod

_PROJECT_COLUMNS = ["id", "goal", "phase", "created_at", "closed_at", "branch"]
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
    deps: dict[tuple, dict] = {}
    node_commits: dict[tuple, dict] = {}
    actual_touches: dict[tuple, dict] = {}
    plan_revisions: dict[tuple, dict] = {}
    agent_spend: dict[tuple, dict] = {}
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
        elif etype == "dep.added":
            deps[(payload["node_id"], payload["depends_on"])] = {}
        elif etype == "commit.linked":
            node_id = ev.get("node_id")
            files = payload.get("files") or []
            # INSERT OR IGNORE on the write side: the first link wins.
            node_commits.setdefault((node_id, payload["sha"]), {"files": json.dumps(files)})
            for path in files:
                actual_touches[(node_id, path)] = {}
        elif etype == "revision.proposed":
            plan_revisions[(ev.get("project_id"), payload["n"])] = {"approved": False}
        elif etype == "revision.approved":
            plan_revisions[(ev.get("project_id"), payload["n"])] = {
                "approved": True, "_approved_ts": ev.get("ts"),
            }
        elif etype == "spend.recorded":
            key = (payload["project_id"], payload["agent"], payload["unit"])
            row = agent_spend.setdefault(key, {"spent": 0.0})
            row["spent"] += payload["amount"]
        # decision.*/external_ref.* events don't affect this replay state.
    return {
        "projects": projects, "nodes": nodes, "components": components, "notes": notes,
        "deps": deps, "node_commits": node_commits, "actual_touches": actual_touches,
        "plan_revisions": plan_revisions, "agent_spend": agent_spend,
    }


def rebuild_state(conn: sqlite3.Connection) -> dict:
    """Replay `events` and return {"projects": {id: row_dict}, "nodes": {...}}."""
    events = [dict(r) for r in db_mod.query_all(conn, "SELECT * FROM events ORDER BY id ASC")]
    return rebuild_state_from_events(events)


def live_state(conn: sqlite3.Connection) -> dict:
    projects = {
        row["id"]: {c: row[c] for c in _PROJECT_COLUMNS}
        for row in db_mod.query_all(conn, "SELECT * FROM projects")
    }
    nodes = {
        row["id"]: {c: row[c] for c in _NODE_COLUMNS}
        for row in db_mod.query_all(conn, "SELECT * FROM nodes")
    }
    components = {
        row["id"]: {c: row[c] for c in _COMPONENT_COLUMNS}
        for row in db_mod.query_all(conn, "SELECT * FROM components")
    }
    notes = {
        row["id"]: {c: row[c] for c in _NOTE_COLUMNS}
        for row in db_mod.query_all(conn, "SELECT * FROM notes")
    }
    deps = {(r["node_id"], r["depends_on"]): {} for r in db_mod.query_all(
        conn,
        "SELECT * FROM deps",
    )}
    node_commits = {
        (r["node_id"], r["sha"]): {"files": r["files"]}
        for r in db_mod.query_all(conn, "SELECT * FROM node_commits")
    }
    actual_touches = {
        (r["node_id"], r["path"]): {} for r in db_mod.query_all(
            conn,
            "SELECT * FROM actual_touches",
        )
    }
    plan_revisions = {
        (r["project_id"], r["n"]): {"approved": r["approved_at"] is not None}
        for r in db_mod.query_all(conn, "SELECT * FROM plan_revisions")
    }
    agent_spend = {
        (r["project_id"], r["agent"], r["unit"]): {"spent": r["spent"]}
        for r in db_mod.query_all(conn, "SELECT * FROM agent_spend")
    }
    return {
        "projects": projects, "nodes": nodes, "components": components, "notes": notes,
        "deps": deps, "node_commits": node_commits, "actual_touches": actual_touches,
        "plan_revisions": plan_revisions, "agent_spend": agent_spend,
    }


_TABLE_COLUMNS = {
    "projects": _REPLAYABLE_PROJECT_COLUMNS,
    "nodes": _REPLAYABLE_NODE_COLUMNS,
    "components": _REPLAYABLE_COMPONENT_COLUMNS,
    "notes": _REPLAYABLE_NOTE_COLUMNS,
    "deps": [],
    "node_commits": ["files"],
    "actual_touches": [],
    "plan_revisions": ["approved"],
    "agent_spend": ["spent"],
}

ALL_REPLAYED_TABLES = tuple(_TABLE_COLUMNS)


def _normalise(table: str, row: dict) -> dict:
    if table == "agent_spend":
        return {"spent": round(float(row.get("spent") or 0), 9)}
    return row


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
            live_row = _normalise(table, {c: live_row_full.get(c) for c in cols})
            replayed_row = _normalise(table, {c: r_table[id_].get(c) for c in cols})
            if replayed_row != live_row:
                mismatches.setdefault(table, {})[id_] = {
                    "live": live_row,
                    "replayed": replayed_row,
                }
    return mismatches


def diff_state(conn: sqlite3.Connection) -> dict:
    """Return {} if replay-from-events equals the live DB, else a dict of
    mismatches for debugging. Covers every table in `ALL_REPLAYED_TABLES`."""
    replayed = rebuild_state(conn)
    live = live_state(conn)
    return _diff_tables(replayed, live, tables=ALL_REPLAYED_TABLES)


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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _write_rows(conn: sqlite3.Connection, table: str, replayed: dict[int, dict],
                keep_live: tuple[str, ...] = ()) -> None:
    """Make `table` hold exactly the replayed rows: update existing ids,
    insert missing ones, delete extras. Snapshot keys that aren't real
    columns (an older event's retired column) are dropped; `keep_live`
    columns are never overwritten on an existing row."""
    columns = _table_columns(conn, table)
    with db_mod.write_txn(conn):
        live_ids = {r["id"] for r in db_mod.query_all(conn, f"SELECT id FROM {table}")}
        for id_ in live_ids - set(replayed):
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (id_,))
        for id_, snapshot in replayed.items():
            row = {k: v for k, v in snapshot.items() if k in columns}
            if id_ in live_ids:
                sets = [k for k in row if k != "id" and k not in keep_live]
                if sets:
                    conn.execute(
                        f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        [row[k] for k in sets] + [id_],
                    )
            else:
                cols = list(row)
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [row[c] for c in cols],
                )


def apply_rebuild(conn: sqlite3.Connection) -> dict:
    """`muvue rebuild --apply` (v4 section 3: "`rebuild` reconstructs the
    replayable set and re-derives the rest"). Rewrites every table in
    `ALL_REPLAYED_TABLES` from the event log in one write transaction,
    then rebuilds the FTS indexes. `nodes.lease_until` comes from the
    last snapshot; `notes.last_retrieved_at` (read-side telemetry) keeps
    its live value. Callers should back the DB up first (the CLI does).
    Returns `{"tables": [...]}`, the tables that differed beforehand."""
    from . import events as events_mod

    before = diff_state(conn)
    with db_mod.write_txn(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        replayed = rebuild_state(conn)
        _write_rows(conn, "projects", replayed["projects"])
        _write_rows(conn, "nodes", replayed["nodes"])
        _write_rows(conn, "components", replayed["components"])
        _write_rows(conn, "notes", replayed["notes"], keep_live=("last_retrieved_at",))

        conn.execute("DELETE FROM deps")
        conn.executemany(
            "INSERT INTO deps (node_id, depends_on) VALUES (?, ?)", list(replayed["deps"])
        )
        conn.execute("DELETE FROM node_commits")
        conn.executemany(
            "INSERT INTO node_commits (node_id, sha, files) VALUES (?, ?, ?)",
            [(n, sha, row["files"]) for (n, sha), row in replayed["node_commits"].items()],
        )
        conn.execute("DELETE FROM actual_touches")
        conn.executemany(
            "INSERT INTO actual_touches (node_id, path) VALUES (?, ?)",
            list(replayed["actual_touches"]),
        )
        conn.execute("DELETE FROM agent_spend")
        conn.executemany(
            "INSERT INTO agent_spend (project_id, agent, unit, spent) VALUES (?, ?, ?, ?)",
            [(p, a, u, row["spent"]) for (p, a, u), row in replayed["agent_spend"].items()],
        )
        for (project_id, n), row in replayed["plan_revisions"].items():
            live = conn.execute(
                "SELECT id, approved_at FROM plan_revisions WHERE project_id = ? AND n = ?",
                (project_id, n),
            ).fetchone()
            approved_at = row.get("_approved_ts") if row["approved"] else None
            if live is None:
                conn.execute(
                    "INSERT INTO plan_revisions (project_id, n, approved_at) VALUES (?, ?, ?)",
                    (project_id, n, approved_at),
                )
            elif (live["approved_at"] is not None) != row["approved"]:
                conn.execute(
                    "UPDATE plan_revisions SET approved_at = ? WHERE id = ?",
                    (approved_at, live["id"]),
                )

        for fts in ("notes_fts", "decisions_fts", "components_fts"):
            conn.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
        events_mod.record_event(
            conn, project_id=None, node_id=None, actor="human", actor_evidence="tty",
            type_="rebuild.applied", payload={"tables": sorted(before)},
        )
    return {"tables": sorted(before)}
