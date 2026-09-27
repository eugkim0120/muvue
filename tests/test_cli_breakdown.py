"""The `_breakdown` internal CLI verb (Task 9's API endpoint spawns this
as a subprocess): an agent decomposes a spec/task into children, resolving
each child's sibling-index `depends_on` to a real node id as it's created."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from muvue.cli.main import app
from muvue.core import db as core_db
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core.repo_init import init_repo


def _init_repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


def test_breakdown_creates_tasks_from_fake_agent(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["_breakdown", "--node", str(spec["id"]), "--agent", "fake", "--path", str(repo_root)])

    assert result.exit_code == 0, result.output
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute(
        "SELECT * FROM nodes WHERE parent_id = ? ORDER BY id", (spec["id"],)
    ).fetchall()
    assert len(children) == 3
    assert children[0]["title"] == "Record voice"
    dep_row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?",
        (children[1]["id"], children[0]["id"]),
    ).fetchone()
    assert dep_row["carries"] == "audio frames"
    events = conn.execute("SELECT type FROM events WHERE node_id = ? ORDER BY id", (spec["id"],)).fetchall()
    types = [e["type"] for e in events]
    assert "breakdown.started" in types
    assert "breakdown.finished" in types
    conn.close()
