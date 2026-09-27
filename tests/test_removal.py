from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core import removal
from muvue.core.config import MuvueConfig


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


def test_remove_node_soft_deletes_node_and_children(conn):
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s")
    task = nodes_mod.create_node(conn, project_id=project["id"], kind="task", title="t", parent_id=spec["id"])
    sub = nodes_mod.create_node(conn, project_id=project["id"], kind="subtask", title="sub", parent_id=task["id"])

    removal.remove_node(conn, task["id"], actor="human", actor_evidence="tty")

    assert nodes_mod.get_node(conn, task["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, sub["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, spec["id"])["deleted_at"] is None


def test_remove_node_refuses_after_gate2(conn, config):
    from muvue.core import gates
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)

    with pytest.raises(removal.RemovalError):
        removal.remove_node(conn, task["id"], actor="human", actor_evidence="tty")
