"""P2 acceptance #3/#4: low tier auto-approves at done -> review; a
criteria edit never auto-approves, even post-recompute."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, rebuild
from muvue.core.config import MuvueConfig
from muvue.core.nodes import NodeError


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
    return projects.create_project(conn, goal="review test")


def _ready_task(conn, project, config, *, touches=None, criteria=None):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=criteria or ["passes"], criteria_mode="auto",
        predicted_touches=touches or ["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


def test_low_tier_auto_approves_straight_to_done(conn, project, config):
    task = _ready_task(conn, project, config, touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is True
    assert result["node"]["status"] == "done"
    assert result["node"]["risk_tier"] == "low"


def test_high_tier_stops_at_review_not_auto_approved(conn, project, config):
    config.risk.globs = ["migrations/**"]
    task = _ready_task(conn, project, config, touches=["migrations/0001.sql"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"
    assert result["node"]["risk_tier"] == "high"


def test_test_file_touch_always_flagged_even_if_low_tier(conn, project, config):
    task = _ready_task(conn, project, config, touches=["tests/test_a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"


def test_human_approves_review_moves_to_done(conn, project, config):
    config.risk.globs = ["migrations/**"]
    task = _ready_task(conn, project, config, touches=["migrations/0001.sql"])
    nodes.start(conn, task["id"], owner="agent-1")
    nodes.done(conn, task["id"], owner="agent-1", config=config)
    approved = nodes.approve_review(conn, task["id"])
    assert approved["node"]["status"] == "done"


def test_reject_review_returns_to_in_progress_with_feedback(conn, project, config):
    config.risk.globs = ["migrations/**"]
    task = _ready_task(conn, project, config, touches=["migrations/0001.sql"])
    nodes.start(conn, task["id"], owner="agent-1")
    nodes.done(conn, task["id"], owner="agent-1", config=config)
    rejected = nodes.reject_review(conn, task["id"], feedback="fix the migration order")
    assert rejected["node"]["status"] == "in_progress"
    notes = conn.execute(
        "SELECT * FROM notes WHERE node_id = ? AND kind = 'feedback'", (task["id"],)
    ).fetchall()
    assert any("migration order" in n["text"] for n in notes)


def test_criteria_edit_after_freeze_never_auto_approves_even_if_diff_would_be_low(
    conn, project, config
):
    """P2 acceptance #4: a criteria edit forces high and that must survive
    done()'s tier recompute -- max_tier(computed, existing) must never
    downgrade a criteria-edit-forced high back to the diff-only tier."""
    task = _ready_task(conn, project, config, touches=["a.py"])  # would compute 'low' alone
    edited = gates.edit_criteria(
        conn, task["id"], criteria_json='["passes", "handles edge case"]'
    )
    assert edited["node"]["risk_tier"] == "high"
    reapproved = gates.approve_node(conn, task["id"], config=config)
    assert reapproved["node"]["risk_tier"] == "high"
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"
    assert result["node"]["risk_tier"] == "high"


def test_done_without_config_preserves_p0_p1_unconditional_behavior(conn, project):
    """No regression: existing call sites that never pass `config`
    (tests/test_idempotency.py, tests/test_rebuild_property.py) keep the
    old unconditional in_progress -> review -> done."""
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    projects.set_phase(conn, project["id"], "executing")
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1")
    assert result["node"]["status"] == "done"


def test_rebuild_matches_live_through_gated_review_flow(conn, project, config):
    """Replay-parity for the new review machinery (plan working rule: any
    new mutating flow must be replayable via core/rebuild.py)."""
    low = _ready_task(conn, project, config, touches=["a.py"])
    high = _ready_task(conn, project, config)
    conn.execute(
        "INSERT INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
        (high["id"], "tests/test_b.py"),
    )
    conn.commit()

    nodes.start(conn, low["id"], owner="agent-1")
    nodes.done(conn, low["id"], owner="agent-1", config=config)

    nodes.start(conn, high["id"], owner="agent-2")
    nodes.done(conn, high["id"], owner="agent-2", config=config)
    nodes.approve_review(conn, high["id"])

    assert rebuild.diff_state(conn) == {}
