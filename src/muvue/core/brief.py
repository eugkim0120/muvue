"""The `brief` wire format (plan v4 section 4, "Brief").

A brief is plain text, one fact per line, so an agent can read it
without parsing and a budget can cut it at a line boundary:

    muvue-brief v1 node:T12 project:P3 cursor:E481 budget:800
    T12 in_progress "Refactor pricing loader" deps:T11 touches:pricing/loader.py tier:low
    criteria auto: "loader reads v2 files" | "tests pass"
    question Q4 "keep the v1 reader?" default:"yes"
    body "Split parsing from IO ..."
    lesson L9 scope:"pricing/*" trigger:"..." failure:"..." do_instead:"..."
    note N31 decision "cache lives in loader, not caller"
    related T11 done "Load raw prices" shared:pricing/loader.py
    decision D2 "Prices are integers of cents" choice:"..."
    component C5 pricing.loader "Reads price files"
    omitted 3

Ordering is priority. The header, the node line, its criteria and open
questions always print. Then, while the budget lasts: the body, lessons
filtered to this node's scope, the node's own notes, nodes that share a
touch path, and finally lexical matches from FTS5 over notes, decisions
and component purposes, best `bm25` first. The budget is characters / 4,
an approximation (about 25% either way across tokenisers), not a billing
number.

`since=EVENT_ID` returns deltas only: the header, the node line, and one
`event` line per relevant event after that id. The header's `cursor` is
the id to pass as the next `since`.
"""

from __future__ import annotations

import json
import sqlite3

from . import db as db_mod
from . import drift as drift_mod
from . import nodes as nodes_mod
from . import risk as risk_mod
from .queries import _fts_query

DEFAULT_BUDGET_TOKENS = 1200
BODY_CHARS = 600
TEXT_CHARS = 240
LEXICAL_LIMIT = 5

# Event types worth an agent's attention in a `since` delta. Metrics,
# request-id bookkeeping and spend are left out.
_DELTA_PREFIXES = ("node.", "question.", "review.", "dep.", "revision.", "replan.")
_DELTA_TYPES = {"note.added", "gate2.approved", "project.phase_changed"}


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _q(text: str | None, limit: int = TEXT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return json.dumps(text, ensure_ascii=False)


def _touches(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        r["path_glob"]
        for r in db_mod.query_all(
            conn, "SELECT path_glob FROM predicted_touches WHERE node_id = ? ORDER BY path_glob",
            (node_id,),
        )
    ]


def _deps(conn: sqlite3.Connection, node_id: int) -> list[int]:
    return [
        r["depends_on"]
        for r in db_mod.query_all(
            conn, "SELECT depends_on FROM deps WHERE node_id = ? ORDER BY depends_on", (node_id,)
        )
    ]


def _node_line(conn: sqlite3.Connection, node: sqlite3.Row) -> str:
    parts = [f"T{node['id']}", node["status"], _q(node["title"])]
    deps = _deps(conn, node["id"])
    if deps:
        parts.append("deps:" + ",".join(f"T{d}" for d in deps))
    touches = _touches(conn, node["id"])
    if touches:
        parts.append("touches:" + ",".join(touches))
    if node["risk_tier"]:
        parts.append(f"tier:{node['risk_tier']}")
    if node["attempts"]:
        parts.append(f"attempts:{node['attempts']}")
    if node["block_reason"]:
        parts.append(f"blocked:{node['block_reason']}")
    return " ".join(parts)


def _criteria_line(node: sqlite3.Row) -> str | None:
    try:
        criteria = json.loads(node["criteria_json"] or "[]")
    except ValueError:
        criteria = []
    if not criteria:
        return None
    mode = node["criteria_mode"]
    label = {"external": "external (not run by muvue)", "manual": "manual (human checks)"}.get(mode, mode)
    return f"criteria {label}: " + " | ".join(_q(str(c)) for c in criteria)


def _lesson_fields(text: str) -> dict:
    try:
        fields = json.loads(text)
    except ValueError:
        fields = None
    if not isinstance(fields, dict):
        return {"failure": text}
    return fields


def _lesson_in_scope(fields: dict, node: sqlite3.Row, touches: list[str], own: bool) -> bool:
    """Lessons from this node always apply. A lesson from anywhere else
    applies when its `scope` names this node or its project, or is a path
    glob that overlaps one of this node's touches."""
    if own:
        return True
    scope = (fields.get("scope") or "").strip()
    if not scope:
        return False
    if scope in (f"node {node['id']}", f"project {node['project_id']}", "*"):
        return True
    return bool(touches) and (
        risk_mod.touches_globs([scope], touches) or risk_mod.touches_globs(touches, [scope])
    )


def _lessons(conn: sqlite3.Connection, node: sqlite3.Row, touches: list[str]) -> list[tuple[int, str]]:
    out = []
    for row in db_mod.query_all(
        conn,
        "SELECT id, node_id, text, pinned FROM notes WHERE kind = 'lesson' "
        "AND archived_at IS NULL ORDER BY id DESC",
    ):
        fields = _lesson_fields(row["text"])
        if not _lesson_in_scope(fields, node, touches, own=row["node_id"] == node["id"]):
            continue
        line = f"lesson L{row['id']}"
        for key in ("scope", "trigger", "failure", "do_instead"):
            if fields.get(key):
                line += f" {key}:{_q(str(fields[key]))}"
        out.append((row["id"], line))
    return out


def _own_notes(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        f"note N{r['id']} {r['kind']}{' pinned' if r['pinned'] else ''} {_q(r['text'])}"
        for r in db_mod.query_all(
            conn,
            "SELECT id, kind, text, pinned FROM notes WHERE node_id = ? AND kind != 'lesson' "
            "AND archived_at IS NULL ORDER BY pinned DESC, id DESC",
            (node_id,),
        )
    ]


def _related(conn: sqlite3.Connection, node: sqlite3.Row, touches: list[str]) -> list[str]:
    """Other nodes in the project whose predicted touches overlap this
    node's, in either glob direction -- the "shared touches first" half
    of the ranking."""
    if not touches:
        return []
    out = []
    for other in db_mod.query_all(
        conn,
        "SELECT * FROM nodes WHERE project_id = ? AND id != ? AND deleted_at IS NULL "
        "AND kind IN ('task', 'subtask') ORDER BY id",
        (node["project_id"], node["id"]),
    ):
        theirs = _touches(conn, other["id"])
        shared = sorted(
            {t for t in theirs if risk_mod.touches_globs([t], touches) or risk_mod.touches_globs(touches, [t])}
        )
        if shared:
            out.append(
                f"related T{other['id']} {other['status']} {_q(other['title'])} shared:{','.join(shared)}"
            )
    return out


def _lexical(conn: sqlite3.Connection, node: sqlite3.Row) -> list[str]:
    """FTS5 over notes (from other nodes), current decisions and current
    component purposes, merged by `bm25` score, best first."""
    query = _fts_query(f"{node['title']} {node['body_md']}")
    if query is None:
        return []
    scored: list[tuple[float, str]] = []
    for r in db_mod.query_all(
        conn,
        "SELECT n.id, n.node_id, n.kind, n.text, bm25(notes_fts) score FROM notes_fts f "
        "JOIN notes n ON n.id = f.rowid WHERE notes_fts MATCH ? AND n.node_id != ? "
        "AND n.kind != 'lesson' AND n.archived_at IS NULL ORDER BY score LIMIT ?",
        (query, node["id"], LEXICAL_LIMIT),
    ):
        scored.append((r["score"], f"note N{r['id']} T{r['node_id']} {r['kind']} {_q(r['text'])}"))
    for r in db_mod.query_all(
        conn,
        "SELECT d.id, d.title, d.choice, bm25(decisions_fts) score FROM decisions_fts f "
        "JOIN decisions d ON d.id = f.rowid WHERE decisions_fts MATCH ? AND d.status = 'current' "
        "ORDER BY score LIMIT ?",
        (query, LEXICAL_LIMIT),
    ):
        scored.append((r["score"], f"decision D{r['id']} {_q(r['title'])} choice:{_q(r['choice'])}"))
    for r in db_mod.query_all(
        conn,
        "SELECT c.id, c.name, c.purpose, bm25(components_fts) score FROM components_fts f "
        "JOIN components c ON c.id = f.rowid WHERE components_fts MATCH ? AND c.status = 'current' "
        "ORDER BY score LIMIT ?",
        (query, LEXICAL_LIMIT),
    ):
        scored.append((r["score"], f"component C{r['id']} {r['name']} {_q(r['purpose'])}"))
    scored.sort(key=lambda s: s[0])
    return [line for _, line in scored[:LEXICAL_LIMIT]]


def _delta_lines(conn: sqlite3.Connection, project_id: int, since: int) -> list[str]:
    out = []
    for e in db_mod.query_all(
        conn,
        "SELECT id, type, node_id, actor, payload FROM events WHERE id > ? "
        "AND (project_id = ? OR project_id IS NULL) ORDER BY id",
        (since, project_id),
    ):
        if not (e["type"].startswith(_DELTA_PREFIXES) or e["type"] in _DELTA_TYPES):
            continue
        line = f"event E{e['id']} {e['type']} " + (f"T{e['node_id']}" if e["node_id"] else "-")
        line += f" by:{e['actor']}"
        try:
            payload = json.loads(e["payload"] or "{}")
        except ValueError:
            payload = {}
        if e["type"].startswith("node.") and isinstance(payload, dict) and payload.get("status"):
            line += f" status:{payload['status']}"
        elif e["type"] == "note.added" and isinstance(payload, dict):
            line += f" {payload.get('kind', '')} {_q(payload.get('text'))}"
        elif e["type"].startswith("question.") and isinstance(payload, dict):
            line += f" {_q(payload.get('text') or payload.get('answer'))}"
        out.append(line)
    return out


def render_brief(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    budget: int = DEFAULT_BUDGET_TOKENS,
    since: int | None = None,
) -> dict:
    """Returns `{"text", "cursor", "tokens", "omitted"}`. Records a
    retrieval for each lesson the brief actually contains (lesson decay,
    plan section 9)."""
    node = nodes_mod.get_node(conn, node_id)
    cursor = db_mod.query_one(conn, "SELECT COALESCE(MAX(id), 0) m FROM events")["m"]
    touches = _touches(conn, node_id)
    header = (
        f"muvue-brief v1 node:T{node_id} project:P{node['project_id']} "
        f"cursor:E{cursor} budget:{budget}"
        + (f" since:E{since}" if since is not None else "")
    )
    required = [header, _node_line(conn, node)]

    lesson_ids: dict[str, int] = {}
    if since is not None:
        optional = _delta_lines(conn, node["project_id"], since)
    else:
        criteria = _criteria_line(node)
        if criteria:
            required.append(criteria)
        for q in db_mod.query_all(
            conn,
            "SELECT id, text, default_answer FROM questions WHERE node_id = ? AND status = 'open' "
            "ORDER BY id",
            (node_id,),
        ):
            required.append(f"question Q{q['id']} {_q(q['text'])} default:{_q(q['default_answer'])}")
        optional = []
        if (node["body_md"] or "").strip():
            optional.append(f"body {_q(node['body_md'], BODY_CHARS)}")
        for lesson_id, line in _lessons(conn, node, touches):
            lesson_ids[line] = lesson_id
            optional.append(line)
        optional += _own_notes(conn, node_id)
        optional += _related(conn, node, touches)
        optional += _lexical(conn, node)

    lines = list(required)
    used = tokens("\n".join(lines))
    omitted = 0
    for i, line in enumerate(optional):
        remaining = len(optional) - i - 1
        # Leave room for the trailing "omitted N" line if anything after
        # this one would be dropped.
        reserve = tokens(f"\nomitted {remaining}") if remaining else 0
        cost = tokens("\n" + line)
        if omitted == 0 and used + cost + reserve <= budget:
            lines.append(line)
            used += cost
        else:
            omitted += 1
    if omitted:
        lines.append(f"omitted {omitted}")

    for line in lines:
        if line in lesson_ids:
            drift_mod.record_lesson_retrieval(conn, lesson_ids[line], node["project_id"])

    text = "\n".join(lines) + "\n"
    return {"text": text, "cursor": cursor, "tokens": tokens(text), "omitted": omitted}
