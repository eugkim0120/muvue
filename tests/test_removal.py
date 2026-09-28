from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core import rebuild
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


def test_remove_leaves_no_rebuild_drift(conn):
    """Critical #1 regression: `node.removed` used to carry only
    `{"node_id": ...}`, which `rebuild.rebuild_state_from_events` folds as
    a full-row snapshot -- `nodes[snapshot["id"]]` then raised
    `KeyError: 'id'`. After the fix, a rebuild right after a removal must
    replay cleanly with zero drift."""
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(conn, project_id=project["id"], kind="task", title="t")

    removal.remove_node(conn, task["id"], actor="human", actor_evidence="tty")

    assert rebuild.diff_state(conn) == {}


def test_remove_cascades_through_multiple_generations(conn):
    """Task 6's parked gap: a top-level ancestor's removal must cascade
    past a direct child, all the way to a grandchild (not just one hop)."""
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s")
    task = nodes_mod.create_node(conn, project_id=project["id"], kind="task", title="t", parent_id=spec["id"])
    sub = nodes_mod.create_node(conn, project_id=project["id"], kind="subtask", title="sub", parent_id=task["id"])

    result = removal.remove_node(conn, spec["id"], actor="human", actor_evidence="tty")

    assert set(result["removed"]) == {spec["id"], task["id"], sub["id"]}
    assert nodes_mod.get_node(conn, spec["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, task["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, sub["id"])["deleted_at"] is not None


def test_remove_refuses_when_a_descendant_is_gate2_approved(conn, config):
    """Important #2: `remove_node` only checked the root's `criteria_hash`,
    not any descendant's -- a spec never gets a criteria_hash of its own,
    so removing a spec whose child task was already Gate-2 approved
    silently destroyed frozen work. Now the whole removal is refused if
    ANY node in the subtree is approved."""
    from muvue.core import gates

    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t", parent_id=spec["id"],
        criteria=["passes"], criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)

    with pytest.raises(removal.RemovalError):
        removal.remove_node(conn, spec["id"], actor="human", actor_evidence="tty")

    assert nodes_mod.get_node(conn, spec["id"])["deleted_at"] is None
    assert nodes_mod.get_node(conn, task["id"])["deleted_at"] is None
