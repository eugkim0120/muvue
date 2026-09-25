"""Project-level mutations. All go through muvue.core (plan working rule 3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import actor as actor_mod
from . import db as db_mod
from . import events
from . import gitutil


def create_project(
    conn: sqlite3.Connection,
    *,
    goal: str,
    actor: str = "human",
    actor_evidence: str = "tty",
    repo_root: Path | None = None,
    follows: list[int] | None = None,
    supersedes: list[int] | None = None,
) -> sqlite3.Row:
    """v4 section 5 (branch coherence): when `repo_root` is given, records
    the branch currently checked out there into `projects.branch`, the
    baseline `core.nodes.start`/`core.doctor.run_doctor` later compare
    against. `None` (no `repo_root`, or `git` couldn't resolve a branch)
    means "no baseline recorded" -- the coherence check is then a no-op
    everywhere it's consulted, matching every pre-v4 call site's
    behavior (most tests, and any caller that doesn't pass `repo_root`).

    `follows`/`supersedes` write `project_links` rows (plan section 3)
    from the new project to each named existing project."""
    branch = gitutil.current_branch(repo_root) if repo_root is not None else None
    with db_mod.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO projects (goal, phase, branch) VALUES (?, 'planning', ?)",
            (goal, branch),
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
        for kind, targets in (("follows", follows), ("supersedes", supersedes)):
            for dst in targets or []:
                get_project(conn, dst)  # LookupError if it doesn't exist
                conn.execute(
                    "INSERT OR IGNORE INTO project_links (src, dst, kind) VALUES (?, ?, ?)",
                    (project_id, dst, kind),
                )
                events.record_event(
                    conn, project_id=project_id, node_id=None, actor=actor,
                    actor_evidence=actor_evidence, type_="project.linked",
                    payload={"src": project_id, "dst": dst, "kind": kind},
                )
        return row


def get_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    row = db_mod.query_one(conn, "SELECT * FROM projects WHERE id = ?", (project_id,))
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


def pause_project(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    repo_root: Path | None = None,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> dict:
    """Emergency stop (plan section 5): refuse `start`, flip the dashboard
    red, and kill the project's runner processes. The phase change is
    committed before runners are signalled, so a runner that is mid-node
    sees `paused` and stops cleanly; its driver subprocesses are killed and
    their nodes released to `ready` (see `core.runners.stop`)."""
    actor_mod.require_human(actor, actor_evidence)
    row = set_phase(conn, project_id, "paused", actor=actor, actor_evidence=actor_evidence)
    stopped: list[int] = []
    if repo_root is not None:
        from . import runners as runners_mod

        stopped = runners_mod.stop(repo_root, project_id)
    return {"project": dict(row), "stopped_runners": stopped}


def resume_project(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> dict:
    """Reverse `pause_project`: back to `executing`. Only a paused project
    can be resumed (a planning project still needs Gate 2)."""
    actor_mod.require_human(actor, actor_evidence)
    if get_project(conn, project_id)["phase"] != "paused":
        raise ValueError(f"project {project_id} is not paused")
    row = set_phase(conn, project_id, "executing", actor=actor, actor_evidence=actor_evidence)
    return {"project": dict(row)}
