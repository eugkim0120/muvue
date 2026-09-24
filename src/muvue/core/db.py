"""Single write path: sqlite connection helpers (WAL, busy_timeout, FTS5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import SCHEMA_SQL, SCHEMA_VERSION


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas required by plan section 1/2."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create (or open) the muvue database and ensure schema is present."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
