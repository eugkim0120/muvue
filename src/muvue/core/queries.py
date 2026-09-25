"""Read-only query helpers backing the agent verbs P0 stubbed
(`brief`/`show`/`status`) and `note`'s CLI wiring (P3: the MCP stdio
server needs real implementations for all of section 4's agent verbs,
not just the ones with prior CLI coverage -- see docs/decisions.md).

It also holds the dashboard's derived reads: a node's diff, the tail of
its driver log, and the touch-drift KPI.

No mutation happens here; this module exists so `cli/main.py` and
`mcp_server.py` share one query implementation instead of duplicating
the row-assembly `muvue.api.app.show_node` already established the shape
for (node + notes + commits + predicted_touches)."""

from __future__ import annotations

import fnmatch
import re
import sqlite3
import subprocess
from collections import deque
from pathlib import Path

from . import db as db_mod
from . import drift as drift_mod
from . import nodes as nodes_mod
from . import risk as risk_mod

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
    rows = db_mod.query_all(
        conn,
        "SELECT d.* FROM decisions_fts f JOIN decisions d ON d.id = f.rowid "
        "WHERE decisions_fts MATCH ? AND d.status = 'current' "
        "ORDER BY bm25(decisions_fts) LIMIT ?",
        (query, limit),
    )
    return _rows_to_list(rows)


def search_components(
    conn: sqlite3.Connection, text: str, limit: int = STRUCTURE_SEARCH_LIMIT
) -> list[dict]:
    """Same as `search_decisions`, over `components.name`/`purpose`."""
    query = _fts_query(text)
    if query is None:
        return []
    rows = db_mod.query_all(
        conn,
        "SELECT c.* FROM components_fts f JOIN components c ON c.id = f.rowid "
        "WHERE components_fts MATCH ? AND c.status = 'current' "
        "ORDER BY bm25(components_fts) LIMIT ?",
        (query, limit),
    )
    return _rows_to_list(rows)


def show_node(conn: sqlite3.Connection, node_id: int) -> dict:
    """Full detail for one node: the row, its notes, linked commits, and
    predicted touches. Same shape as `muvue.api.app`'s `GET
    /nodes/{id}`."""
    node = nodes_mod.get_node(conn, node_id)
    notes = _rows_to_list(
        db_mod.query_all(conn, "SELECT * FROM notes WHERE node_id = ? ORDER BY id", (node_id,))
    )
    commits = _rows_to_list(
        db_mod.query_all(conn, "SELECT * FROM node_commits WHERE node_id = ?", (node_id,))
    )
    touches = [
        r["path_glob"]
        for r in db_mod.query_all(
            conn,
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?",
            (node_id,),
        )
    ]
    return {
        "node": dict(node), "notes": notes, "commits": commits, "predicted_touches": touches,
        "verification": _VERIFICATION[node["criteria_mode"]],
    }


# v4 section 5: muvue runs `auto` criteria itself; `external` ones run in
# the agent's environment and are shown as unverified; `manual` waits
# for a human.
_VERIFICATION = {"auto": "checked_by_muvue", "external": "unverified", "manual": "human"}


def unverified_external(conn: sqlite3.Connection) -> list[dict]:
    """Nodes in review whose criteria muvue could not run (`external`):
    the inbox lists them so a human checks before approving."""
    return [
        {"node_id": r["id"], "project_id": r["project_id"], "title": r["title"]}
        for r in db_mod.query_all(
            conn,
            "SELECT id, project_id, title FROM nodes WHERE status = 'review' "
            "AND criteria_mode = 'external' AND deleted_at IS NULL ORDER BY id",
        )
    ]


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
        db_mod.query_all(
            conn,
            "SELECT * FROM questions WHERE node_id = ? AND status = 'open'",
            (node_id,),
        )
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
        rows = db_mod.query_all(
            conn,
            "SELECT status, COUNT(*) c FROM nodes WHERE project_id = ? "
            "AND deleted_at IS NULL GROUP BY status",
            (project_id,),
        )
    else:
        rows = db_mod.query_all(
            conn,
            "SELECT status, COUNT(*) c FROM nodes WHERE deleted_at IS NULL GROUP BY status",
        )
    return {"project_id": project_id, "counts": {r["status"]: r["c"] for r in rows}}


DIFF_MAX_LINES = 3000


def _git_out(cwd: str | Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def node_diff(conn: sqlite3.Connection, node_id: int, repo_root: str | Path) -> dict:
    """The node panel's diff (plan section 8). A node with linked
    commits shows their patches. Otherwise a node with a live worktree
    shows its work so far against its branch point, uncommitted edits
    included. `source` says which, or `"none"`."""
    node = nodes_mod.get_node(conn, node_id)
    shas = risk_mod.node_commit_shas(conn, node_id)
    worktree = node["worktree"] if node["worktree"] and Path(node["worktree"]).exists() else None
    text, source = "", "none"
    if shas:
        # Strict-mode commits live in the airlock; its worktree can read them.
        for cwd in filter(None, (repo_root, worktree)):
            patches = [_git_out(cwd, "show", "--format=commit %H%n%s%n", sha) for sha in shas]
            if all(p is not None for p in patches):
                text, source = "\n".join(patches), "commits"
                break
    elif worktree:
        head = (_git_out(repo_root, "rev-parse", "HEAD") or "").strip()
        base = (_git_out(worktree, "merge-base", "HEAD", head) or "").strip() if head else ""
        diff = _git_out(worktree, "diff", base or "main")
        if diff is not None:
            text, source = diff, "worktree"
    lines = text.splitlines()
    truncated = len(lines) > DIFF_MAX_LINES
    if truncated:
        text = "\n".join(lines[:DIFF_MAX_LINES]) + "\n"
    return {"node_id": node_id, "source": source, "diff": text, "truncated": truncated, "commits": shas}


def tail_log(repo_root: str | Path, node_id: int, lines: int) -> str:
    """The last `lines` lines of a node's driver output: the runner's
    `<id>.log`, then `run-<id>.log` from a dashboard-started run."""
    logs = Path(repo_root) / ".muvue" / "logs"
    tail: deque[str] = deque(maxlen=max(lines, 0))
    for name in (f"run-{node_id}.log", f"{node_id}.log"):
        path = logs / name
        if path.exists():
            with open(path, errors="replace") as f:
                tail.extend(f)
    return "".join(tail)


def touch_drift(conn: sqlite3.Connection) -> float:
    """Prediction-vs-actual touch drift (plan section 8 KPIs): over the
    nodes with recorded actual touches, the mean share of those paths no
    predicted glob covers. 0.0 when nothing has been committed yet."""
    predicted: dict[int, list[str]] = {}
    for row in db_mod.query_all(conn, "SELECT node_id, path_glob FROM predicted_touches"):
        predicted.setdefault(row["node_id"], []).append(row["path_glob"])
    actual: dict[int, list[str]] = {}
    for row in db_mod.query_all(conn, "SELECT node_id, path FROM actual_touches"):
        actual.setdefault(row["node_id"], []).append(row["path"])
    shares = [
        sum(not any(fnmatch.fnmatch(p, g) for g in predicted.get(node_id, [])) for p in paths) / len(paths)
        for node_id, paths in actual.items()
    ]
    return sum(shares) / len(shares) if shares else 0.0
