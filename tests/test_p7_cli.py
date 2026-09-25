"""CLI-level smoke tests for P7's `muvue audit` (real implementation,
replacing the P0 `NOT IMPLEMENTED` stub) and `muvue migrate`'s new
`notes.archived_at` `ALTER TABLE` step (SCHEMA_VERSION 2 -> 3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from muvue.core.schema import SCHEMA_VERSION
from muvue.core import db as core_db, drift
from muvue.core.repo_init import init_repo


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        cwd=repo_root, capture_output=True, text=True,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
        },
    )


def _init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "anchored.py").write_text("value = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def test_audit_cli_drafts_inbox_item_on_known_drift(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    conn.close()

    result = _run(repo, "audit")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert len(body["drafted"]) >= 1

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        rows = conn2.execute(
            "SELECT * FROM events WHERE type = 'inbox.audit_drift_signal'"
        ).fetchall()
        assert len(rows) >= 1
    finally:
        conn2.close()


def test_migrate_adds_archived_at_column_to_pre_existing_db(tmp_path: Path):
    """Simulate a database created before P7 (no `notes.archived_at`):
    build one against a schema with that column stripped out, then run
    `muvue migrate` and confirm it's added without data loss."""
    repo = _init_git_repo(tmp_path / "repo")
    init_repo(repo)
    db_path = repo / ".muvue" / "muvue.db"
    conn = core_db.connect(db_path)
    # Simulate pre-P7: drop the column P7 added (SQLite >= 3.35 supports
    # DROP COLUMN) so `migrate` has real work to do.
    conn.execute("ALTER TABLE notes DROP COLUMN archived_at")
    conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}
    assert "archived_at" not in cols_before
    conn.close()

    result = _run(repo, "migrate")
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(db_path)
    try:
        cols_after = {r["name"] for r in conn2.execute("PRAGMA table_info(notes)")}
        assert "archived_at" in cols_after
        version = conn2.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert version == str(SCHEMA_VERSION)
    finally:
        conn2.close()
