"""`migrate`: bring an existing .muvue/muvue.db up to the current schema.

P0 only ships schema_version 1, so this is idempotent-apply-current-schema
plus a version bump record; future phases add real migration steps here.
"""

from __future__ import annotations

from pathlib import Path

from . import db as core_db
from .schema import SCHEMA_VERSION


def run_migrate(repo_root: Path) -> int:
    """Returns the schema_version after migration."""
    db_path = Path(repo_root) / ".muvue" / "muvue.db"
    conn = core_db.init_db(db_path)  # re-applies CREATE TABLE IF NOT EXISTS
    try:
        current = core_db.get_schema_version(conn)
        if current < SCHEMA_VERSION:
            conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
            current = SCHEMA_VERSION
        return current
    finally:
        conn.close()
