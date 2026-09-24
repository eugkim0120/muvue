"""Project-level mutations. All go through muvue.core (plan working rule 3)."""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import events


def create_project(
    conn: sqlite3.Connection,
    *,
    goal: str,
    budget_unit: str = "usd",
    budget_limit: float = 0,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> sqlite3.Row:
    with db_mod.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO projects (goal, phase, budget_unit, budget_limit, spent) "
            "VALUES (?, 'planning', ?, ?, 0)",
            (goal, budget_unit, budget_limit),
        )
        project_id = cur.lastrowid
        row = get_project(conn, project_id)
        events.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="project.created",
            payload=dict(row),
        )
        return row


def get_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise LookupError(f"no such project: {project_id}")
    return row


def set_phase(
    conn: sqlite3.Connection,
    project_id: int,
    phase: str,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> sqlite3.Row:
    """Move a project between phases directly (planning/executing/paused/
    closed). Gate 2 approval (core.gates.approve_gate2) is the normal path
    from planning -> executing; this exists for the other transitions
    (pause/resume/close, ships P2+) and for tests that need an
    already-executing project without running the full Gate flow.

    v4 section 3 adds `projects.closed_at`: set (once) the first time
    `phase` becomes `'closed'`, left alone on every other transition
    (including a hypothetical re-close) so it records *when the project
    first closed*, not "the last time set_phase(closed) ran"."""
    with db_mod.write_txn(conn):
        if phase == "closed":
            conn.execute(
                "UPDATE projects SET phase = ?, closed_at = COALESCE(closed_at, "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE id = ?",
                (phase, project_id),
            )
        else:
            conn.execute("UPDATE projects SET phase = ? WHERE id = ?", (phase, project_id))
        row = get_project(conn, project_id)
        events.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="project.phase_changed",
            payload=dict(row),
        )
        return row
