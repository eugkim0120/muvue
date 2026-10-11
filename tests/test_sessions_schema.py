"""Mission control phase 1: the `sessions` and `session_touches` tables
(schema version 9) and the `[sessions]` liveness thresholds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from muvue.core import db as core_db, migrate
from muvue.core.config import DEFAULT_CONFIG_TOML, ConfigError, SessionsConfig, load_config

SESSION_COLUMNS = {
    "id", "project_id", "node_id", "agent", "origin", "cwd", "branch", "transcript_path",
    "pid", "started_at", "last_event_at", "last_action", "ended_at", "waiting", "alias_of",
}


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_new_database_has_the_sessions_tables(tmp_path: Path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    try:
        assert {"sessions", "session_touches"} <= _tables(conn)
        assert {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")} == SESSION_COLUMNS
        assert {r["name"] for r in conn.execute("PRAGMA table_info(session_touches)")} == {"session_id", "path", "at"}
    finally:
        conn.close()


def test_origin_is_launched_or_hand(tmp_path: Path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (id, agent, origin, cwd, started_at, last_event_at) "
                "VALUES ('s', 'claude', 'robot', '/x', 't', 't')"
            )
    finally:
        conn.close()


def test_migrate_from_version_8_creates_the_sessions_tables(tmp_path: Path):
    db_path = tmp_path / ".muvue" / "muvue.db"
    db_path.parent.mkdir()
    conn = core_db.init_db(db_path)
    conn.execute("DROP TABLE session_touches")
    conn.execute("DROP TABLE sessions")
    conn.execute("UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    assert migrate.run_migrate(tmp_path) == 9

    conn = core_db.connect(db_path)
    try:
        assert {"sessions", "session_touches"} <= _tables(conn)
    finally:
        conn.close()


def test_the_default_config_has_the_session_thresholds(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(path)
    assert (cfg.sessions.working_s, cfg.sessions.idle_s, cfg.sessions.ended_s) == (120, 900, 7200)
    assert cfg.sessions == SessionsConfig()


def test_thresholds_out_of_order_are_rejected(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML.replace("idle_s = 900", "idle_s = 60"))
    with pytest.raises(ConfigError, match="working_s < idle_s < ended_s"):
        load_config(path)


def test_an_unknown_sessions_key_is_rejected(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML.replace("ended_s = 7200", "ended_s = 7200\nstale_s = 5"))
    with pytest.raises(ConfigError, match="stale_s"):
        load_config(path)
