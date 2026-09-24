"""P1 acceptance #5: revision v2 approval leaves untouched nodes unchanged
(diff-only approval)."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, revisions
from muvue.core.config import MuvueConfig


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
    return projects.create_project(conn, goal="revisions")


def _task(conn, project, title):
    return nodes.create_node(
        conn, project_id=project["id"], kind="task", title=title,
        criteria=["works"], criteria_mode="auto", predicted_touches=["a.py"],
        status="pending",
    )


def test_revision_v2_approval_leaves_untouched_nodes_unchanged(conn, project, config):
    task_a = _task(conn, project, "task a")
    task_b = _task(conn, project, "task b")

    # v1: both tasks proposed and approved as the initial plan revision
    # (pending -> ready, criteria frozen).
    revisions.propose_revision(conn, project["id"], [task_a["id"], task_b["id"]])
    revisions.approve_revision(conn, project["id"], 1, config=config)

    before_a = dict(nodes.get_node(conn, task_a["id"]))
    before_b = dict(nodes.get_node(conn, task_b["id"]))

    # v2: only task_a's criteria change; a new task_c is added. task_b is
    # untouched by the diff.
    edited_a = gates.edit_criteria(
        conn, task_a["id"], criteria_json='["works", "handles nulls"]'
    )
    assert edited_a["node"]["status"] == "pending"  # bumped back for re-approval

    task_c = _task(conn, project, "task c")

    revisions.propose_revision(
        conn, project["id"], [task_a["id"], task_b["id"], task_c["id"]]
    )
    diff = revisions.diff_revision(conn, project["id"], 2)
    assert diff["added"] == [task_c["id"]]
    assert diff["changed"] == [task_a["id"]]
    assert diff["unchanged"] == [task_b["id"]]
    assert diff["removed"] == []

    result = revisions.approve_revision(conn, project["id"], 2, config=config)
    assert set(result["touched_node_ids"]) == {task_a["id"], task_c["id"]}

    after_a = dict(nodes.get_node(conn, task_a["id"]))
    after_b = dict(nodes.get_node(conn, task_b["id"]))
    after_c = dict(nodes.get_node(conn, task_c["id"]))

    # task_b: untouched by the diff -> status and criteria_hash identical.
    assert after_b["status"] == before_b["status"] == "ready"
    assert after_b["criteria_hash"] == before_b["criteria_hash"]

    # task_a: re-validated and re-approved, hash refreshed.
    assert after_a["status"] == "ready"
    assert after_a["criteria_hash"] != before_a["criteria_hash"]

    # task_c: newly added, approved for the first time.
    assert after_c["status"] == "ready"
    assert after_c["criteria_hash"] is not None


def test_diff_revision_n1_has_no_previous(conn, project, config):
    task_a = _task(conn, project, "task a")
    revisions.propose_revision(conn, project["id"], [task_a["id"]])
    diff = revisions.diff_revision(conn, project["id"], 1)
    assert diff["added"] == [task_a["id"]]
    assert diff["unchanged"] == []


def test_approve_revision_twice_is_noop(conn, project, config):
    task_a = _task(conn, project, "task a")
    revisions.propose_revision(conn, project["id"], [task_a["id"]])
    r1 = revisions.approve_revision(conn, project["id"], 1, config=config)
    assert r1["noop"] is False
    r2 = revisions.approve_revision(conn, project["id"], 1, config=config)
    assert r2["noop"] is True


def test_replan_adds_subtask_without_new_approval(conn, project, config):
    task = _task(conn, project, "parent task")
    gates.approve_gate2(conn, project["id"], config=config)
    sub = revisions.replan_add_subtask(conn, parent_task_id=task["id"], title="extra step")
    assert sub["status"] == "ready"
    assert sub["kind"] == "subtask"


def test_replan_refuses_on_unapproved_task(conn, project):
    task = _task(conn, project, "parent task")
    with pytest.raises(gates.GateError):
        revisions.replan_add_subtask(conn, parent_task_id=task["id"], title="extra step")
