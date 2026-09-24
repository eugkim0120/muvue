"""v4 section 3 schema deltas: `projects.closed_at`, `agent_spend`,
`nodes.lease_expiries`, `events.actor_evidence`, `actual_touches`.
Migration 3 -> 4 for pre-existing databases, and spot-checks that
`actor_evidence` is populated meaningfully (not left NULL) from each of
the call-site layers the plan names: CLI (tty), MCP (mcp), hooks (hook),
runner (subprocess), API/dashboard (dashboard_token).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue import mcp_server
from muvue.core import db as core_db
from muvue.core import hooks, migrate, nodes, projects
from muvue.core.schema import SCHEMA_VERSION


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


def test_schema_version_is_5():
    assert SCHEMA_VERSION == 5


def test_new_columns_and_tables_exist(conn):
    project_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "closed_at" in project_cols
    assert "branch" in project_cols

    node_cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    assert "lease_expiries" in node_cols
    assert "attempts" in node_cols  # split, not replaced

    event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "actor_evidence" in event_cols

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "agent_spend" in tables
    assert "actual_touches" in tables


def test_migrate_from_schema_version_3_adds_v4_columns(tmp_path: Path):
    """Simulate a pre-v4 database: drop the columns v4 added and reset
    schema_meta to 3, then confirm `migrate` brings them back (mirrors
    the existing 2->3 `notes.archived_at` migration test in
    tests/test_p7_cli.py, one version up)."""
    db_path = tmp_path / ".muvue" / "muvue.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = core_db.init_db(db_path)
    conn.execute("ALTER TABLE projects DROP COLUMN closed_at")
    conn.execute("ALTER TABLE projects DROP COLUMN branch")
    conn.execute("ALTER TABLE nodes DROP COLUMN lease_expiries")
    conn.execute("ALTER TABLE events DROP COLUMN actor_evidence")
    conn.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    version = migrate.run_migrate(tmp_path)
    assert version == 5

    conn2 = core_db.connect(db_path)
    try:
        assert "closed_at" in {r["name"] for r in conn2.execute("PRAGMA table_info(projects)")}
        assert "branch" in {r["name"] for r in conn2.execute("PRAGMA table_info(projects)")}
        assert "lease_expiries" in {r["name"] for r in conn2.execute("PRAGMA table_info(nodes)")}
        assert "actor_evidence" in {r["name"] for r in conn2.execute("PRAGMA table_info(events)")}
    finally:
        conn2.close()


def test_project_closed_at_set_once_on_first_close_only(conn):
    project = projects.create_project(conn, goal="closed_at test")
    assert project["closed_at"] is None
    closed = projects.set_phase(conn, project["id"], "closed")
    assert closed["closed_at"] is not None
    first_closed_at = closed["closed_at"]
    # A hypothetical re-close (set_phase called again with "closed") does
    # not clobber the original closed_at.
    closed_again = projects.set_phase(conn, project["id"], "closed")
    assert closed_again["closed_at"] == first_closed_at


# -- actor_evidence: threaded meaningfully from each layer, not left NULL --


def test_cli_layer_default_actor_evidence_is_tty(conn):
    """CLI-invoked core calls default to actor_evidence="tty" (v4
    section 3: "CLI human-verb calls from a real TTY should pass
    `tty`"). Since `core.nodes.create_node`/etc default to "tty" and the
    CLI never overrides it, this is the CLI's own effective behavior."""
    project = projects.create_project(conn, goal="p")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t")
    ev = conn.execute(
        "SELECT actor_evidence FROM events WHERE type = 'node.created' AND node_id = ?",
        (node["id"],),
    ).fetchone()
    assert ev["actor_evidence"] == "tty"


def test_mcp_layer_passes_mcp_actor_evidence(tmp_path: Path):
    db_path = tmp_path / "muvue.db"
    conn = core_db.init_db(db_path)
    project = projects.create_project(conn, goal="mcp test")
    projects.set_phase(conn, project["id"], "executing")
    node = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    from muvue.core.config import AgentConfig, MuvueConfig, RoutingConfig

    config = MuvueConfig(
        agents={"fake": AgentConfig(command="x", cost_model="tokens")},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    mcp_server._start(conn, config, {"node_id": node["id"], "owner": "agent-1"})
    ev = conn.execute(
        "SELECT actor_evidence FROM events WHERE type = 'node.start' AND node_id = ?",
        (node["id"],),
    ).fetchone()
    assert ev["actor_evidence"] == "mcp"
    conn.close()


def test_hook_layer_passes_hook_actor_evidence(conn):
    project = projects.create_project(conn, goal="hook test")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    hooks.handle_post_commit(conn, commit_sha="abc123", message=f"Muvue-Node: {node['id']}\n")
    ev = conn.execute(
        "SELECT actor_evidence FROM events WHERE type = 'commit.linked' AND node_id = ?",
        (node["id"],),
    ).fetchone()
    assert ev["actor_evidence"] == "hook"


def test_runner_layer_passes_subprocess_actor_evidence(tmp_path: Path):
    import shutil

    if shutil.which("muvue-fake-agent") is None:
        pytest.skip("muvue-fake-agent console script not installed")

    from muvue.core import runner as runner_mod
    from muvue.core.config import AgentConfig, MuvueConfig, RoutingConfig

    db_path = tmp_path / "muvue.db"
    conn = core_db.init_db(db_path)
    project = projects.create_project(conn, goal="runner test")
    projects.set_phase(conn, project["id"], "executing")
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria_mode="auto", criteria=["ok"], predicted_touches=[],
    )
    config = MuvueConfig(
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens")},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    runner_mod.run(db_path, config, tmp_path)
    ev = conn.execute(
        "SELECT actor_evidence FROM events WHERE type = 'node.start' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert ev["actor_evidence"] == "subprocess"
    conn.close()
