"""v4 section 2/3: `agent_spend` (one row per project/driver/unit) and
`core.spend`'s minimal increment/read helper. Budget *enforcement*
against a configured `[agents.<x>.budget]` limit is out of scope this
phase (see core/spend.py's module docstring) -- these tests only cover
accumulation and read-back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import projects, spend


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="spend test")


def test_record_spend_creates_a_row_on_first_call(conn, project):
    row = spend.record_spend(conn, project["id"], "claude", "usd", 1.5)
    assert row["project_id"] == project["id"]
    assert row["agent"] == "claude"
    assert row["unit"] == "usd"
    assert row["spent"] == 1.5


def test_record_spend_accumulates_across_calls(conn, project):
    spend.record_spend(conn, project["id"], "claude", "usd", 1.5)
    spend.record_spend(conn, project["id"], "claude", "usd", 2.5)
    assert spend.get_spend(conn, project["id"], "claude", "usd") == 4.0


def test_record_spend_keeps_agents_and_units_independent(conn, project):
    """"Budget is per driver, in that driver's unit. No cross-unit
    arithmetic." (v4 section 6) -- two different (agent, unit) pairs
    under the same project never share a row."""
    spend.record_spend(conn, project["id"], "claude", "requests", 10)
    spend.record_spend(conn, project["id"], "codex", "usd", 3.0)
    assert spend.get_spend(conn, project["id"], "claude", "requests") == 10
    assert spend.get_spend(conn, project["id"], "codex", "usd") == 3.0
    assert spend.get_spend(conn, project["id"], "claude", "usd") == 0.0


def test_get_spend_is_zero_for_an_untouched_driver(conn, project):
    assert spend.get_spend(conn, project["id"], "nonexistent", "usd") == 0.0


def test_project_spend_lists_every_row_for_a_project(conn, project):
    spend.record_spend(conn, project["id"], "claude", "requests", 5)
    spend.record_spend(conn, project["id"], "codex", "usd", 1.0)
    rows = [dict(r) for r in spend.project_spend(conn, project["id"])]
    assert {(r["agent"], r["unit"], r["spent"]) for r in rows} == {
        ("claude", "requests", 5.0),
        ("codex", "usd", 1.0),
    }


def test_record_spend_appends_a_spend_recorded_event(conn, project):
    spend.record_spend(conn, project["id"], "claude", "usd", 2.0)
    types = [r["type"] for r in conn.execute("SELECT type FROM events ORDER BY id")]
    assert "spend.recorded" in types
