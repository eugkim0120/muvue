"""P5 acceptance criterion 3 (plan section 11 row P5, section 6 "Handoff"):
after a pause, the driver can be handed off to an interactive session and
continue purely from DB state -- no in-memory handoff (plan principle 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects, rebuild
from muvue.core.nodes import HumanOnly, NodeError
from muvue.core.state_machine import InvalidTransition, NotLeaseOwner


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="handoff test")
    return projects.set_phase(conn, p["id"], "executing")


def _ready_task(conn, project, **kw):
    return nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", **kw,
    )


def test_handoff_reassigns_owner_of_an_in_progress_node(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    result = nodes.handoff(conn, task["id"], new_owner="human-jane")
    row = result["node"]
    assert row["status"] == "in_progress"
    assert row["owner"] == "human-jane"
    assert row["lease_until"] is not None


def test_handed_off_node_can_be_finished_by_the_new_owner(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.handoff(conn, task["id"], new_owner="human-jane")
    result = nodes.done(conn, task["id"], owner="human-jane")
    assert result["node"]["status"] == "done"


def test_original_owner_can_no_longer_finish_after_handoff(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.handoff(conn, task["id"], new_owner="human-jane")
    with pytest.raises((NodeError, InvalidTransition, NotLeaseOwner)):
        nodes.done(conn, task["id"], owner="runner:claude")


def test_handoff_unblocks_a_blocked_node(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.block(conn, task["id"], reason="rate_limit", actor="runner:claude")
    result = nodes.handoff(conn, task["id"], new_owner="human-jane")
    row = result["node"]
    assert row["status"] == "in_progress"
    assert row["owner"] == "human-jane"
    assert row["block_reason"] is None


def test_handoff_refuses_a_node_that_is_not_started_or_blocked(conn, project):
    task = _ready_task(conn, project)
    with pytest.raises(NodeError):
        nodes.handoff(conn, task["id"], new_owner="human-jane")


def test_handoff_refuses_a_done_node(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.done(conn, task["id"], owner="runner:claude")
    with pytest.raises(NodeError):
        nodes.handoff(conn, task["id"], new_owner="human-jane")


def test_handoff_is_a_human_verb(conn, project):
    """Never exposed over MCP (plan section 4) -- refused by core itself
    for a non-human actor, matching every other human verb's enforcement
    (P3 acceptance #4's pattern)."""
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    with pytest.raises(HumanOnly):
        nodes.handoff(conn, task["id"], new_owner="human-jane", actor="agent")


def test_rebuild_matches_live_through_handoff_of_a_blocked_node(conn, project):
    task = _ready_task(conn, project)
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.block(conn, task["id"], reason="rate_limit", actor="runner:claude")
    nodes.handoff(conn, task["id"], new_owner="human-jane")
    assert rebuild.diff_state(conn) == {}
