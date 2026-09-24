"""`migrate`: bring an existing .muvue/muvue.db up to the current schema.

P0 only ships schema_version 1, so this is idempotent-apply-current-schema
plus a version bump record; future phases add real migration steps here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import db as core_db
from .schema import SCHEMA_VERSION


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """`CREATE TABLE IF NOT EXISTS` (re-applied by `core_db.init_db` on
    every migrate) only creates a table that doesn't exist yet -- it
    never adds a column to a table that already exists without it. A
    real `ALTER TABLE` step is needed for a pre-existing database, same
    idempotent-by-inspection pattern as everything else in this module:
    check `PRAGMA table_info` first rather than relying on catching
    sqlite3's "duplicate column name" error."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def run_migrate(repo_root: Path) -> int:
    """Returns the schema_version after migration."""
    db_path = Path(repo_root) / ".muvue" / "muvue.db"
    conn = core_db.init_db(db_path)  # re-applies CREATE TABLE IF NOT EXISTS
    try:
        current = core_db.get_schema_version(conn)
        if current < SCHEMA_VERSION:
            # SCHEMA_VERSION 2 -> 3 (P7): notes.archived_at.
            _add_column_if_missing(conn, "notes", "archived_at", "TEXT")
            # SCHEMA_VERSION 3 -> 4 (v4 txn/schema foundational slice):
            # projects.closed_at, nodes.lease_expiries (split from
            # attempts), events.actor_evidence. `agent_spend` and
            # `actual_touches` are new tables, already covered by
            # `init_db`'s `CREATE TABLE IF NOT EXISTS` re-apply above.
            _add_column_if_missing(conn, "projects", "closed_at", "TEXT")
            _add_column_if_missing(
                conn, "nodes", "lease_expiries", "INTEGER NOT NULL DEFAULT 0"
            )
            _add_column_if_missing(conn, "events", "actor_evidence", "TEXT")
            conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
            current = SCHEMA_VERSION
        return current
    finally:
        conn.close()
