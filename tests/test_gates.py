"""P1 acceptance #1-#3: `start` refused in planning, criteria-edit
re-tiering/re-approval, granularity lint warnings at Gate 2."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, rebuild
from muvue.core.config import MuvueConfig
from muvue.core.nodes import NodeError
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
    return projects.create_project(conn, goal="ship P1")


def _decompose_one_task(conn, project, *, criteria=None, criteria_mode="auto", touches=None):
    return nodes.create_node(
        conn,
        project_id=project["id"],
        kind="task",
        title="do the thing",
        criteria=criteria or ["passes tests"],
        criteria_mode=criteria_mode,
        predicted_touches=touches or ["a.py"],
        status="pending",
    )


# -- acceptance #1: start refused in planning ------------------------------


def test_start_refused_while_project_in_planning(conn, project):
    task = _decompose_one_task(conn, project)
    # Even if the node itself is 'ready' (shouldn't normally happen before
    # Gate 2, but this isolates the phase check), start must still refuse.
    conn.execute("UPDATE nodes SET status = 'ready' WHERE id = ?", (task["id"],))
    conn.commit()
    with pytest.raises(NodeError, match="planning"):
        nodes.start(conn, task["id"], owner="agent-1")


def test_start_allowed_after_gate2_approval(conn, project, config):
    task = _decompose_one_task(conn, project)
    gates.approve_gate2(conn, project["id"], config=config)
    result = nodes.start(conn, task["id"], owner="agent-1")
    assert result["node"]["status"] == "in_progress"


# -- acceptance #2: criteria edit re-tiers and re-approves -----------------


def test_criteria_edit_after_freeze_retiers_and_blocks_start(conn, project, config):
    task = _decompose_one_task(conn, project)
    gates.approve_gate2(conn, project["id"], config=config)
    frozen = nodes.get_node(conn, task["id"])
    assert frozen["risk_tier"] == "low"
    assert frozen["criteria_hash"] is not None

    edited = gates.edit_criteria(
        conn, task["id"], criteria_json='["passes tests", "handles edge case"]'
    )
    assert edited["re_approval_required"] is True
    assert edited["node"]["risk_tier"] == "high"
    assert edited["node"]["status"] == "pending"

    # project is already 'executing' (Gate 2 flips it once); the node
    # itself is back to 'pending', so start must refuse: only
    # ready -> in_progress is a legal edge into in_progress.
    with pytest.raises(InvalidTransition):
        nodes.start(conn, task["id"], owner="agent-1")

    reapproved = gates.approve_node(conn, task["id"], config=config)
    assert reapproved["node"]["status"] == "ready"
    started = nodes.start(conn, task["id"], owner="agent-1")
    assert started["node"]["status"] == "in_progress"
    # risk_tier stays 'high' until a human explicitly lowers it; re-approval
    # only re-freezes criteria and re-admits the node to 'ready'.
    assert started["node"]["risk_tier"] == "high"


def test_rebuild_matches_live_after_criteria_edit(conn, project, config):
    """P3 regression: `node.criteria_edited` nests its row snapshot under
    payload["node"] instead of at the payload's top level (unlike every
    other node.* event). `rebuild.rebuild_state` previously assumed every
    node.* payload *is* the row (`payload["id"]`), which raised KeyError
    the first time rebuild ran after a criteria edit -- no prior test
    combined the two. Fixed in core/rebuild.py to unwrap the nested
    "node" key; this pins that fix."""
    task = _decompose_one_task(conn, project)
    gates.approve_gate2(conn, project["id"], config=config)
    gates.edit_criteria(conn, task["id"], criteria_json='["passes tests", "handles edge case"]')
    assert rebuild.diff_state(conn) == {}


def test_criteria_edit_without_change_does_not_retier(conn, project, config):
    task = _decompose_one_task(conn, project)
    gates.approve_gate2(conn, project["id"], config=config)
    same_json = nodes.get_node(conn, task["id"])["criteria_json"]
    edited = gates.edit_criteria(conn, task["id"], criteria_json=same_json)
    assert edited["re_approval_required"] is False
    assert edited["node"]["risk_tier"] == "low"
    assert edited["node"]["status"] == "ready"


# -- acceptance #3: granularity lint warns, doesn't block -------------------


def test_lint_warns_on_oversized_touches(conn, project, config):
    task = _decompose_one_task(
        conn, project, touches=[f"f{i}.py" for i in range(config.planning.max_files_per_task + 1)]
    )
    result = gates.approve_gate2(conn, project["id"], config=config)
    assert task["id"] in result["warnings"]
    assert any("files" in w for w in result["warnings"][task["id"]])
    # warning does not block approval
    assert nodes.get_node(conn, task["id"])["status"] == "ready"


def test_lint_warns_on_too_many_subtasks(conn, project, config):
    task = _decompose_one_task(conn, project)
    for i in range(config.planning.max_subtasks + 1):
        nodes.create_node(
            conn, project_id=project["id"], parent_id=task["id"], kind="subtask",
            title=f"sub {i}", status="pending",
        )
    result = gates.approve_gate2(conn, project["id"], config=config)
    assert any("subtasks" in w for w in result["warnings"][task["id"]])


def test_lint_warns_on_missing_auto_criterion(conn, project, config):
    task = _decompose_one_task(conn, project, criteria_mode="manual")
    result = gates.approve_gate2(conn, project["id"], config=config)
    assert any("auto" in w for w in result["warnings"][task["id"]])


def test_lint_silent_when_within_limits(conn, project, config):
    task = _decompose_one_task(conn, project)
    result = gates.approve_gate2(conn, project["id"], config=config)
    assert task["id"] not in result["warnings"]


# -- Gate 1 -------------------------------------------------------------


def test_gate1_spec_requires_approval_before_ready(conn, project):
    spec = gates.submit_spec(conn, project_id=project["id"], title="spec", body_md="# spec")
    assert spec["status"] == "pending"
    approved = gates.approve_spec(conn, spec["id"])
    assert approved["status"] == "ready"
