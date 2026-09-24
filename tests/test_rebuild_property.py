"""P0 acceptance #2: replaying all `events` reproduces the live DB state.

Uses randomized sequences of core operations (create/start/done/fail) as a
lightweight property test (no extra dependency beyond the approved stack:
stdlib `random` drives the sequence generation).
"""

import random
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects, rebuild


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


def _executing_project(conn, goal: str):
    """P1 refuses `start` while project.phase == 'planning' (Gate 2 not yet
    approved). These P0-era property tests exercise the node lifecycle
    directly, so flip phase straight to 'executing' as if Gate 2 had
    already passed."""
    project = projects.create_project(conn, goal=goal)
    return projects.set_phase(conn, project["id"], "executing")


def test_rebuild_matches_live_after_scripted_sequence(conn):
    project = _executing_project(conn, "rebuild test project")
    n1 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t1", status="ready")
    n2 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t2", status="ready")
    n3 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t3", status="ready")

    nodes.start(conn, n1["id"], owner="alice")
    nodes.done(conn, n1["id"], owner="alice")

    nodes.start(conn, n2["id"], owner="bob")
    nodes.fail(conn, n2["id"], owner="bob", lesson="flaky test", trigger="ci", do_instead="retry", scope="t2")

    nodes.start(conn, n3["id"], owner="carol", request_id="dup-1")
    nodes.start(conn, n3["id"], owner="carol", request_id="dup-1")  # duplicate, no-op

    assert rebuild.diff_state(conn) == {}


@pytest.mark.parametrize("seed", range(10))
def test_rebuild_matches_live_after_random_sequence(conn, seed):
    rng = random.Random(seed)
    project = _executing_project(conn, f"seed-{seed}")
    node_ids = [
        nodes.create_node(
            conn, project_id=project["id"], kind="task", title=f"n{i}", status="ready",
            max_attempts=2,
        )["id"]
        for i in range(5)
    ]
    owner = "agent-x"
    for node_id in node_ids:
        action = rng.choice(["start_done", "start_fail_fail", "start_only"])
        if action == "start_done":
            nodes.start(conn, node_id, owner=owner)
            nodes.done(conn, node_id, owner=owner)
            if rng.random() < 0.5:
                nodes.done(conn, node_id, owner=owner)  # redundant done, no-op
        elif action == "start_fail_fail":
            nodes.start(conn, node_id, owner=owner)
            nodes.fail(conn, node_id, owner=owner, lesson="l1")
            row = nodes.get_node(conn, node_id)
            if row["status"] == "ready":
                nodes.start(conn, node_id, owner=owner)
                nodes.fail(conn, node_id, owner=owner, lesson="l2")
        else:
            nodes.start(conn, node_id, owner=owner)

        assert rebuild.diff_state(conn) == {}, f"mismatch after acting on node {node_id}"

    assert rebuild.diff_state(conn) == {}
