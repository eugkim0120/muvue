"""`migrate` refuses a database it can't safely upgrade (written by a
newer muvue, or with an unreadable version) without touching it, and
backs up the database before it upgrades one. `doctor` reports an
unreadable version instead of crashing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import doctor, migrate
from muvue.core.repo_init import init_repo
from muvue.core.schema import SCHEMA_VERSION


def _set_version(repo: Path, value: str) -> None:
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (value,))
    conn.close()


def _tables(repo: Path) -> list[str]:
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    names = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master ORDER BY name")]
    conn.close()
    return names


@pytest.mark.parametrize("value", [str(SCHEMA_VERSION + 1), "abc"])
def test_migrate_refuses_without_touching_the_database(tmp_path: Path, value: str):
    init_repo(tmp_path)
    _set_version(tmp_path, value)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    conn.execute("DROP TABLE project_links")  # anything migrate would recreate
    conn.close()
    before = _tables(tmp_path)
    with pytest.raises(migrate.MigrateError):
        migrate.run_migrate(tmp_path)
    assert _tables(tmp_path) == before


def test_cli_migrate_reports_a_newer_database_as_one_line(tmp_path: Path):
    init_repo(tmp_path)
    _set_version(tmp_path, str(SCHEMA_VERSION + 1))
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "migrate", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert result.stderr.startswith("error: ") and "newer" in result.stderr


def test_cli_migrate_backs_up_before_upgrading(tmp_path: Path):
    init_repo(tmp_path)
    _set_version(tmp_path, str(SCHEMA_VERSION - 1))
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "migrate", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    backups = list((tmp_path / ".muvue").glob("muvue.db.bak-*"))
    assert len(backups) == 1
    assert str(backups[0]) in result.stdout


def test_doctor_reports_an_unreadable_schema_version(tmp_path: Path):
    init_repo(tmp_path)
    _set_version(tmp_path, "abc")
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("'abc'" in issue for issue in report.issues)
