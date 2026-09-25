"""History export (plan section 2 file layout, section 9, P6):
`.muvue/history/<project-id>.jsonl.gz` -- a gzip-compressed,
newline-delimited-JSON archive of one project's full `events` slice.

`muvue export` writes these archives: one project with `--project-id`,
or every project plus `unscoped.jsonl.gz` without it (`export_all`).
This module is the per-project archive format P6 needs: what `close`
(`core/close.py`) writes before archiving a project, and what
`rebuild_from_archive` replays to reproduce that project's final state
without the live DB (P6 acceptance #3) -- reusing
`core.rebuild.rebuild_state_from_events`'s fold logic against this
filtered event set rather than duplicating it (see docs/decisions.md).
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import rebuild as rebuild_mod


def export_project_events(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = db_mod.query_all(
        conn,
        "SELECT * FROM events WHERE project_id = ? ORDER BY id ASC",
        (project_id,),
    )
    return [dict(r) for r in rows]


def archive_path(repo_root: str | Path, project_id: int) -> Path:
    return Path(repo_root) / ".muvue" / "history" / f"{project_id}.jsonl.gz"


def export_project(conn: sqlite3.Connection, project_id: int, repo_root: str | Path) -> Path:
    """Write this project's full event history to
    `.muvue/history/<project_id>.jsonl.gz`, one JSON event per line."""
    events = export_project_events(conn, project_id)
    out_path = archive_path(repo_root, project_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, default=str) + "\n")
    return out_path


UNSCOPED_ARCHIVE = "unscoped.jsonl.gz"


def export_all(conn: sqlite3.Connection, repo_root: str | Path) -> list[tuple[Path, int]]:
    """Whole-DB export in the section 2 layout: one `<project-id>.jsonl.gz`
    per project, plus `unscoped.jsonl.gz` for events that belong to no
    project. Returns `[(path, event_count), ...]`."""
    written = []
    for row in db_mod.query_all(conn, "SELECT id FROM projects ORDER BY id"):
        path = export_project(conn, row["id"], repo_root)
        written.append((path, len(export_project_events(conn, row["id"]))))
    unscoped = [
        dict(r) for r in db_mod.query_all(
            conn, "SELECT * FROM events WHERE project_id IS NULL ORDER BY id ASC"
        )
    ]
    out_path = Path(repo_root) / ".muvue" / "history" / UNSCOPED_ARCHIVE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for ev in unscoped:
            f.write(json.dumps(ev, default=str) + "\n")
    written.append((out_path, len(unscoped)))
    return written


def read_archive(path: str | Path) -> list[dict]:
    events: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def rebuild_from_archive(path: str | Path) -> dict:
    """Project-scoped replay (P6 acceptance #3): reproduce a project's
    final {"projects": {...}, "nodes": {...}} state purely from its
    exported archive, reusing `core.rebuild`'s single fold implementation
    against this filtered event set instead of the live DB's full
    `events` table."""
    events = read_archive(path)
    return rebuild_mod.rebuild_state_from_events(events)
