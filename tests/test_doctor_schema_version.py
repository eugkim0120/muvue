"""`doctor` fails on a database older (or newer) than this muvue's
schema: without `muvue migrate`, CLI calls run against missing columns
and tables while doctor reported `ok`."""

from __future__ import annotations

from pathlib import Path

from muvue.core import db as core_db
from muvue.core import doctor
from muvue.core.repo_init import init_repo
from muvue.core.schema import SCHEMA_VERSION


def _set_version(repo: Path, version: int) -> None:
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    conn.close()


def test_doctor_fails_on_an_unmigrated_database(tmp_path: Path):
    init_repo(tmp_path)
    _set_version(tmp_path, SCHEMA_VERSION - 2)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("muvue migrate" in e for e in report.issues)


def test_doctor_fails_on_a_database_from_a_newer_muvue(tmp_path: Path):
    init_repo(tmp_path)
    _set_version(tmp_path, SCHEMA_VERSION + 1)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("newer" in e for e in report.issues)


def test_doctor_ok_on_the_current_schema(tmp_path: Path):
    init_repo(tmp_path)
    assert doctor.run_doctor(tmp_path, skip_security_probes=True).ok is True
