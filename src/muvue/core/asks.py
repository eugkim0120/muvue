"""`ask` / `wait` protocol (plan section 4 protocol, section 5 enforcement).

`ask` creates an inbox-style question record with a proposed default
answer. `wait` resolves it: if a human already answered, the answer
becomes a `feedback` note; if unanswered and past
`planning.ask_timeout_minutes`, either the proposed default is applied
(`--default-ok`) or the node moves to `blocked(question)`.

`wait` takes an injectable `now` so tests can simulate "past ask_timeout"
by manipulating timestamps instead of sleeping for real minutes (plan
section 5, P1 acceptance #4).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import events
from . import nodes as nodes_mod

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class AskError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)


def get_question(conn: sqlite3.Connection, question_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        raise AskError(f"no such question: {question_id}")
    return row


def ask(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    question: str,
    default: str | None,
    actor: str = "agent",
    request_id: str | None = None,
) -> dict:
    node = nodes_mod.get_node(conn, node_id)
    dup = events.find_recent_by_request_id(conn, request_id, "question.asked") if request_id else None
    if dup is not None:
        return {"noop": True, "question": json.loads(dup["payload"])}

    cur = conn.execute(
        "INSERT INTO questions (node_id, project_id, text, default_answer) "
        "VALUES (?, ?, ?, ?)",
        (node_id, node["project_id"], question, default),
    )
    question_id = cur.lastrowid
    row = get_question(conn, question_id)
    events.record_event(
        conn,
        project_id=node["project_id"],
        node_id=node_id,
        actor=actor,
        type_="question.asked",
        payload=dict(row),
        request_id=request_id,
    )
    conn.commit()
    return {"noop": False, "question": dict(row)}


def answer(
    conn: sqlite3.Connection, question_id: int, *, text: str, actor: str = "human"
) -> dict:
    q = get_question(conn, question_id)
    if q["status"] != "open":
        return {"noop": True, "question": dict(q)}
    conn.execute(
        "UPDATE questions SET status = 'answered', answer = ?, "
        "answered_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
        (text, question_id),
    )
    nodes_mod.add_note(conn, q["node_id"], kind="feedback", text=text, actor=actor)
    row = get_question(conn, question_id)
    events.record_event(
        conn,
        project_id=q["project_id"],
        node_id=q["node_id"],
        actor=actor,
        type_="question.answered",
        payload=dict(row),
    )
    conn.commit()
    return {"noop": False, "question": dict(row)}


def wait(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    timeout_minutes: int,
    default_ok: bool = False,
    now: datetime | None = None,
) -> dict:
    q = get_question(conn, question_id)
    if q["status"] == "answered":
        return {"status": "answered", "answer": q["answer"]}
    if q["status"] == "timed_out":
        return {"status": "blocked"}

    current = now or _now()
    elapsed = current - _parse(q["created_at"])
    if elapsed < timedelta(minutes=timeout_minutes):
        return {"status": "pending"}

    if default_ok:
        conn.execute(
            "UPDATE questions SET status = 'answered', answer = ?, "
            "answered_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (q["default_answer"], question_id),
        )
        nodes_mod.add_note(
            conn, q["node_id"], kind="feedback", text=q["default_answer"] or "", actor="agent"
        )
        events.record_event(
            conn,
            project_id=q["project_id"],
            node_id=q["node_id"],
            actor="agent",
            type_="question.default_applied",
            payload={"question_id": question_id, "answer": q["default_answer"]},
        )
        conn.commit()
        return {"status": "default_applied", "answer": q["default_answer"]}

    conn.execute("UPDATE questions SET status = 'timed_out' WHERE id = ?", (question_id,))
    node = nodes_mod.get_node(conn, q["node_id"])
    nodes_mod.block(
        conn, q["node_id"], reason="question", actor=node["owner"] or "agent"
    )
    conn.commit()
    return {"status": "blocked"}
