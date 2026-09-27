"""Task 10: `core.agent_status.agent_status` -- per-agent roles, current
work and spend for the dashboard's Agents panel (design spec's "Runs
section" is covered separately by `GET /nodes/{id}/runs` in test_api.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import agent_status, nodes as nodes_mod, projects as projects_mod, spend as spend_mod
from muvue.core.config import MuvueConfig


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


def test_agent_status_lists_configured_agents_with_roles_and_spend(conn):
    config = MuvueConfig()  # default routes everything to "claude" per RoutingConfig's defaults
    project = projects_mod.create_project(conn, goal="g")
    spend_mod.record_spend(conn, project["id"], "claude", "tokens", 1.5)

    rows = agent_status.agent_status(conn, project["id"], config)

    claude_row = next(r for r in rows if r["agent"] == "claude")
    assert set(claude_row["roles"]) == {"spec", "task", "subtask"}
    assert claude_row["current"] is None
    assert any(s["unit"] == "tokens" and s["spent"] == 1.5 for s in claude_row["spend"])


def test_agent_status_shows_current_work(conn):
    config = MuvueConfig()
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="in_progress", owner="claude",
    )
    rows = agent_status.agent_status(conn, project["id"], config)
    claude_row = next(r for r in rows if r["agent"] == "claude")
    assert claude_row["current"]["node_id"] == task["id"]
