"""P3 acceptance #1/#2: a second driver cannot take an already-leased
node, and a mid-task human edit (bumping `nodes.version`) fails `done`
for the original lease holder instead of silently overwriting it."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, rebuild
from muvue.core.config import MuvueConfig
from muvue.core.nodes import NodeError, VersionMismatch
from muvue.core.state_machine import InvalidTransition


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="p3 leases")


def _ready_task(conn, project, config, **kw):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto",
        predicted_touches=["a.py"], status="pending", **kw,
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


# -- acceptance #1: second driver cannot take a leased node -----------------


def test_second_owner_cannot_start_an_already_leased_node(conn, project, config):
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A")
    with pytest.raises((NodeError, InvalidTransition)):
        nodes.start(conn, task["id"], owner="agent-B")
    # not reassigned: still owned by agent-A
    row = nodes.get_node(conn, task["id"])
    assert row["owner"] == "agent-A"
    assert row["status"] == "in_progress"


def test_second_owner_gets_precise_leased_error_message(conn, project, config):
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A")
    with pytest.raises(NodeError, match="leased by 'agent-A'"):
        nodes.start(conn, task["id"], owner="agent-B")


def test_same_owner_restart_still_refused_not_a_special_case(conn, project, config):
    """Re-`start`ing your own already-in_progress lease is also refused
    (no (in_progress, in_progress) edge) -- `start` is not idempotent by
    identity, only by --request-id (see test_idempotency.py)."""
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A")
    with pytest.raises(InvalidTransition):
        nodes.start(conn, task["id"], owner="agent-A")


def test_second_owner_can_start_after_lease_expires_and_reconciles(conn, project, config):
    from muvue.core import daemon

    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A", lease_minutes=-1)
    daemon.reconcile_leases(conn)
    result = nodes.start(conn, task["id"], owner="agent-B")
    assert result["node"]["owner"] == "agent-B"


# -- acceptance #2: mid-task human edit fails `done` (version mismatch) -----


def test_human_note_after_start_bumps_version_and_fails_done(conn, project, config):
    task = _ready_task(conn, project, config)
    started = nodes.start(conn, task["id"], owner="agent-A")
    captured_version = started["node"]["version"]

    # human comment/note lands mid-task (e.g. via the API's /comment
    # endpoint, which calls add_note(actor="human")).
    nodes.add_note(conn, task["id"], kind="feedback", text="please also handle X", actor="human")

    with pytest.raises(VersionMismatch):
        nodes.done(conn, task["id"], owner="agent-A", config=config, expected_version=captured_version)

    # not silently overwritten: node is untouched (still in_progress)
    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "in_progress"
    assert row["version"] > captured_version


def test_done_without_expected_version_is_unaffected_by_edits(conn, project, config):
    """Backward compatible: omitting --version/expected_version preserves
    existing behavior for callers that don't opt in to the check."""
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A")
    nodes.add_note(conn, task["id"], kind="feedback", text="fyi", actor="human")
    result = nodes.done(conn, task["id"], owner="agent-A", config=config)
    assert result["node"]["status"] == "done"


def test_done_with_matching_version_succeeds(conn, project, config):
    task = _ready_task(conn, project, config)
    started = nodes.start(conn, task["id"], owner="agent-A")
    result = nodes.done(
        conn, task["id"], owner="agent-A", config=config,
        expected_version=started["node"]["version"],
    )
    assert result["node"]["status"] == "done"


def test_agent_authored_notes_do_not_bump_version(conn, project, config):
    """Only human-visible edits bump version; an agent's own lesson/
    discovery notes on its own leased node are not 'someone else changed
    this under me'."""
    task = _ready_task(conn, project, config)
    started = nodes.start(conn, task["id"], owner="agent-A")
    nodes.add_note(conn, task["id"], kind="discovery", text="found the bug", actor="agent")
    result = nodes.done(
        conn, task["id"], owner="agent-A", config=config,
        expected_version=started["node"]["version"],
    )
    assert result["node"]["status"] == "done"


def test_rebuild_matches_live_through_lease_and_version_flows(conn, project, config):
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="agent-A")
    nodes.add_note(conn, task["id"], kind="feedback", text="edit", actor="human")
    try:
        nodes.done(conn, task["id"], owner="agent-A", config=config, expected_version=1)
    except VersionMismatch:
        pass
    nodes.done(conn, task["id"], owner="agent-A", config=config)
    assert rebuild.diff_state(conn) == {}
