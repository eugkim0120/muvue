"""Read-only query helpers backing the agent verbs P0 stubbed
(`brief`/`show`/`status`) and `note`'s CLI wiring (P3: the MCP stdio
server needs real implementations for all of section 4's agent verbs,
not just the ones with prior CLI coverage -- see docs/decisions.md).

No mutation happens here; this module exists so `cli/main.py` and
`mcp_server.py` share one query implementation instead of duplicating
the row-assembly `muvue.api.app.show_node` already established the shape
for (node + notes + commits + predicted_touches)."""

from __future__ import annotations

import re
import sqlite3

from . import drift as drift_mod
from . import nodes as nodes_mod

STRUCTURE_SEARCH_LIMIT = 3


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _fts_query(text: str) -> str | None:
    """Turn free text (a node's own title/body) into an FTS5 MATCH query:
    every alphanumeric word of length > 2, quoted (so punctuation/FTS5
    syntax characters in the source text can't be interpreted as query
    operators) and OR'd together, so a hit on *any* shared term surfaces
    the row -- ranked afterwards by `bm25()`. `None` if there's nothing
    worth searching on."""
    words = re.findall(r"[A-Za-z0-9_]+", text or "")
    words = [w for w in words if len(w) > 2]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


def search_decisions(
    conn: sqlite3.Connection, text: str, limit: int = STRUCTURE_SEARCH_LIMIT
) -> list[dict]:
    """FTS5 search over `decisions.title`/`context`/`choice` (plan
    section 4: `brief`'s ranking "over notes, decisions, component
    purposes"), current (non-superseded) decisions only, ranked by
    `bm25()` -- best match first."""
    query = _fts_query(text)
    if query is None:
        return []
    rows = conn.execute(
        "SELECT d.* FROM decisions_fts f JOIN decisions d ON d.id = f.rowid "
        "WHERE decisions_fts MATCH ? AND d.status = 'current' "
        "ORDER BY bm25(decisions_fts) LIMIT ?",
        (query, limit),
    ).fetchall()
    return _rows_to_list(rows)


def search_components(
    conn: sqlite3.Connection, text: str, limit: int = STRUCTURE_SEARCH_LIMIT
) -> list[dict]:
    """Same as `search_decisions`, over `components.name`/`purpose`."""
    query = _fts_query(text)
    if query is None:
        return []
    rows = conn.execute(
        "SELECT c.* FROM components_fts f JOIN components c ON c.id = f.rowid "
        "WHERE components_fts MATCH ? AND c.status = 'current' "
        "ORDER BY bm25(components_fts) LIMIT ?",
        (query, limit),
    ).fetchall()
    return _rows_to_list(rows)


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
    4: `fail`'s lesson is meant to be read back on the next attempt), any
    open question still awaiting an answer, and (P6) structure-aware
    context pulled by FTS5 over the node's own title/body: prior
    `decisions` and `components` whose text overlaps -- e.g. a decision
    made and `close`d on an earlier, unrelated project. This is what the
    Claude Code adapter's SessionStart hook calls (see
    adapters.claude_code)."""
    detail = show_node(conn, node_id)
    # Archived lessons (P7 lesson decay) are excluded from what's
    # surfaced -- an archived, unpinned lesson is exactly the one that
    # wasn't retrieved enough to earn a spot in a fresh brief either.
    lessons = [
        n for n in detail["notes"]
        if (n["kind"] == "lesson" or n["pinned"]) and not n.get("archived_at")
    ]
    # P7 lesson decay (plan section 9): every lesson note surfaced here
    # counts as one retrieval by this node's project --
    # `core.drift.decay_lessons` archives a lesson not retrieved by
    # enough distinct projects, unless pinned.
    for n in lessons:
        if n["kind"] == "lesson":
            drift_mod.record_lesson_retrieval(conn, n["id"], detail["node"]["project_id"])
    open_questions = _rows_to_list(
        conn.execute(
            "SELECT * FROM questions WHERE node_id = ? AND status = 'open'", (node_id,)
        ).fetchall()
    )
    query_text = f"{detail['node']['title']} {detail['node']['body_md']}"
    relevant_decisions = search_decisions(conn, query_text)
    relevant_components = search_components(conn, query_text)
    return {
        **detail,
        "lessons": lessons,
        "open_questions": open_questions,
        "relevant_decisions": relevant_decisions,
        "relevant_components": relevant_components,
    }


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
