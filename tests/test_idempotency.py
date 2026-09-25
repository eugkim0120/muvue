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


@pytest.mark.parametrize("verb", ["done", "fail", "ask", "start"])
def test_request_id_dedupe_check_runs_inside_the_write_txn(conn, node_row, monkeypatch, verb):
    """Regression: the dedupe lookup ran *before* `write_txn`, so two
    concurrent calls with the same request id could both pass it and both
    apply. Checking inside `BEGIN IMMEDIATE` serialises them."""
    from muvue.core import asks, events

    seen = []
    real = events.find_recent_by_request_id

    def spy(c, request_id, type_):
        seen.append(c.in_transaction)
        return real(c, request_id, type_)

    monkeypatch.setattr(events, "find_recent_by_request_id", spy)
    if verb == "done":
        nodes.done(conn, node_row["id"], owner="alice", request_id="r")
    elif verb == "fail":
        nodes.fail(conn, node_row["id"], owner="alice", lesson="x", request_id="r")
    elif verb == "ask":
        asks.ask(conn, node_row["id"], question="q?", default="d", request_id="r")
    else:
        other = nodes.create_node(conn, project_id=node_row["project_id"], kind="task",
                                  title="t2", status="ready")
        nodes.start(conn, other["id"], owner="bob", request_id="r")
    assert seen and all(seen)
