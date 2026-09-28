import sqlite3
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import migrate as migrate_mod
from muvue.core.schema import SCHEMA_VERSION


def _init_repo(tmp_path: Path) -> Path:
    (tmp_path / ".muvue").mkdir()
    return tmp_path


def test_deps_gains_carries_column_on_migrate(tmp_path):
    repo_root = _init_repo(tmp_path)
    db_path = repo_root / ".muvue" / "muvue.db"
    # Simulate a pre-existing DB at schema version 7: init at the
    # current schema, then drop the column migrate.py is about to add,
    # so migrate has real work to do.
    conn = core_db.init_db(db_path)
    conn.execute("ALTER TABLE deps RENAME TO deps_old")
    conn.execute(
        "CREATE TABLE deps (node_id INTEGER NOT NULL, depends_on INTEGER NOT NULL, "
        "PRIMARY KEY (node_id, depends_on))"
    )
    conn.execute("INSERT INTO deps SELECT node_id, depends_on FROM deps_old")
    conn.execute("DROP TABLE deps_old")
    conn.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    result_version = migrate_mod.run_migrate(repo_root)

    assert result_version == SCHEMA_VERSION
    conn = core_db.connect(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(deps)")}
    assert "carries" in cols
    conn.close()


def test_new_db_has_carries_column(tmp_path):
    repo_root = _init_repo(tmp_path)
    conn = core_db.init_db(repo_root / ".muvue" / "muvue.db")
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(deps)")}
    assert "carries" in cols
    conn.close()
