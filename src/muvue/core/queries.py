"""Read-only query helpers backing the agent verbs P0 stubbed
(`brief`/`show`/`status`) and `note`'s CLI wiring (P3: the MCP stdio
server needs real implementations for all of section 4's agent verbs,
not just the ones with prior CLI coverage -- see docs/decisions.md).

No mutation happens here; this module exists so `cli/main.py` and
`mcp_server.py` share one query implementation instead of duplicating
the row-assembly `muvue.api.app.show_node` already established the shape
for (node + notes + commits + predicted_touches)."""

from __future__ import annotations

import sqlite3

from . import nodes as nodes_mod


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def show_node(conn: sqlite3.Connection, node_id: int) -> dict:
    """Full detail for one node: the row, its notes, linked commits, and
    predicted touches. Same shape as `muvue.api.app`'s `GET
    /nodes/{id}`."""
    node = nodes_mod.get_node(conn, node_id)
    notes = _rows_to_list(
        conn.execute("SELECT * FROM notes WHERE node_id = ? ORDER BY id", (node_id,)).fetchall()
    )
    commits = _rows_to_list(
        conn.execute("SELECT * FROM node_commits WHERE node_id = ?", (node_id,)).fetchall()
    )
    touches = [
        r["path_glob"]
        for r in conn.execute(
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (node_id,)
        ).fetchall()
    ]
    return {"node": dict(node), "notes": notes, "commits": commits, "predicted_touches": touches}


def brief_node(conn: sqlite3.Connection, node_id: int) -> dict:
    """What an agent needs to start work on a node: the node itself, any
    pinned/lesson notes (context carried across attempts -- plan section
    4: `fail`'s lesson is meant to be read back on the next attempt), and
    any open question still awaiting an answer. This is what the Claude
    Code adapter's SessionStart hook calls (see adapters.claude_code)."""
    detail = show_node(conn, node_id)
    lessons = [n for n in detail["notes"] if n["kind"] == "lesson" or n["pinned"]]
    open_questions = _rows_to_list(
        conn.execute(
            "SELECT * FROM questions WHERE node_id = ? AND status = 'open'", (node_id,)
        ).fetchall()
    )
    return {**detail, "lessons": lessons, "open_questions": open_questions}


def status_summary(conn: sqlite3.Connection, project_id: int | None = None) -> dict:
    """Counts of nodes by status, optionally scoped to one project --
    the plain-text "where do things stand" agent verb (plan section 4)."""
    if project_id is not None:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM nodes WHERE project_id = ? "
            "AND deleted_at IS NULL GROUP BY status",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM nodes WHERE deleted_at IS NULL GROUP BY status"
        ).fetchall()
    return {"project_id": project_id, "counts": {r["status"]: r["c"] for r in rows}}
