"""P0 acceptance #4: duplicate `done` (same request-id, or same node
re-done) is a no-op."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def node_row(conn):
    project = projects.create_project(conn, goal="test project")
    # P1 refuses `start` while project.phase == 'planning'; flip straight to
    # 'executing' as if Gate 2 had already passed (this fixture only
    # exercises done()/fail() idempotency, not the gate flow).
    projects.set_phase(conn, project["id"], "executing")
    node = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="do the thing", status="ready"
    )
    nodes.start(conn, node["id"], owner="alice")
    return nodes.get_node(conn, node["id"])


def _count_events(conn, node_id, type_):
    return conn.execute(
        "SELECT COUNT(*) c FROM events WHERE node_id = ? AND type = ?", (node_id, type_)
    ).fetchone()["c"]


def test_duplicate_request_id_is_noop(conn, node_row):
    r1 = nodes.done(conn, node_row["id"], owner="alice", request_id="req-1")
    assert r1["noop"] is False
    assert r1["node"]["status"] == "done"

    r2 = nodes.done(conn, node_row["id"], owner="alice", request_id="req-1")
    assert r2["noop"] is True
    assert r2["node"]["status"] == "done"

    # Only one node.done event recorded despite two calls.
    assert _count_events(conn, node_row["id"], "node.done") == 1


def test_same_node_redone_without_request_id_is_noop(conn, node_row):
    r1 = nodes.done(conn, node_row["id"], owner="alice")
    assert r1["noop"] is False

    r2 = nodes.done(conn, node_row["id"], owner="alice")
    assert r2["noop"] is True
    assert r2["node"]["status"] == "done"

    assert _count_events(conn, node_row["id"], "node.done") == 1


def test_done_by_non_owner_is_rejected(conn, node_row):
    from muvue.core.state_machine import NotLeaseOwner

    with pytest.raises(NotLeaseOwner):
        nodes.done(conn, node_row["id"], owner="mallory")
