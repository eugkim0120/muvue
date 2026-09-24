"""P3: light-mode `review` dispatch on top of P2's tier/flag gate (plan
section 5) -- `auto` criteria run a check (opt-in `run_checks`, real
subprocess by default when wired -- see core/review.py), `external`
criteria are always flagged (can't be verified in-process), `manual`
always waits for a human, regardless of risk tier."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, review
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
    return projects.create_project(conn, goal="light review test")


def _ready_task(conn, project, config, *, criteria_mode="auto", touches=None):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode=criteria_mode,
        predicted_touches=touches or ["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


# -- core.review.dispatch (pure, no DB mutation) -----------------------


def test_dispatch_is_a_noop_outside_light_mode(conn, project, config):
    config.mode = "strict"
    task = _ready_task(conn, project, config, criteria_mode="manual")
    assert review.dispatch(conn, task, config)["flag"] is False


def test_dispatch_flags_manual_unconditionally(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="manual")
    result = review.dispatch(conn, task, config)
    assert result["flag"] is True
    assert result["event_type"] == "review.manual_criteria"


def test_dispatch_flags_external_unconditionally(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="external")
    result = review.dispatch(conn, task, config)
    assert result["flag"] is True
    assert result["event_type"] == "review.external_flagged"


def test_dispatch_auto_without_run_checks_never_flags(conn, project, config):
    """No check runner wired in: preserves P0-P2 behavior exactly (auto
    criteria trusted; `core.risk`'s tier/test-touch gate alone decides)."""
    task = _ready_task(conn, project, config, criteria_mode="auto")
    result = review.dispatch(conn, task, config)
    assert result["flag"] is False


def test_dispatch_auto_flags_on_failing_check(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto")
    result = review.dispatch(conn, task, config, run_checks=lambda cmd, cwd: False)
    assert result["flag"] is True
    assert result["event_type"] == "review.auto_check_failed"


def test_dispatch_auto_does_not_flag_on_passing_check(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto")
    result = review.dispatch(conn, task, config, run_checks=lambda cmd, cwd: True)
    assert result["flag"] is False


# -- integration through nodes.done -----------------------------------


def test_done_manual_criteria_stops_at_review_even_if_low_tier(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="manual", touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"
    assert result["node"]["risk_tier"] == "low"  # tier itself is unaffected, only the gate


def test_done_external_criteria_stops_at_review_even_if_low_tier(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="external", touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"


def test_done_auto_criteria_with_failing_check_stops_at_review(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto", touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(
        conn, task["id"], owner="agent-1", config=config, run_checks=lambda cmd, cwd: False,
    )
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"


def test_done_auto_criteria_with_passing_check_still_auto_approves_low_tier(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto", touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(
        conn, task["id"], owner="agent-1", config=config, run_checks=lambda cmd, cwd: True,
    )
    assert result["auto_approved"] is True
    assert result["node"]["status"] == "done"


def test_done_without_run_checks_preserves_p2_behavior_for_auto_criteria(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto", touches=["a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["auto_approved"] is True


def test_default_run_checks_runs_a_real_subprocess(tmp_path: Path):
    ok = review.default_run_checks("exit 0", str(tmp_path))
    bad = review.default_run_checks("exit 1", str(tmp_path))
    assert ok is True
    assert bad is False


# -- v4 section 5: touches outside predicted_touches raises tier at done ---


def test_done_raises_tier_when_actual_touch_outside_predicted(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto", touches=["src/a.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    conn.execute(
        "INSERT INTO actual_touches (node_id, path) VALUES (?, ?)",
        (task["id"], "src/unexpected.py"),
    )
    conn.commit()
    result = nodes.done(
        conn, task["id"], owner="agent-1", config=config, run_checks=lambda cmd, cwd: True,
    )
    # touch outside predicted_touches raises low -> medium, which stops
    # auto-approval and sends the node to review instead.
    assert result["node"]["risk_tier"] == "medium"
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"


def test_done_does_not_raise_tier_when_actual_touches_within_predicted(conn, project, config):
    task = _ready_task(conn, project, config, criteria_mode="auto", touches=["src/*.py"])
    nodes.start(conn, task["id"], owner="agent-1")
    conn.execute(
        "INSERT INTO actual_touches (node_id, path) VALUES (?, ?)", (task["id"], "src/a.py"),
    )
    conn.commit()
    result = nodes.done(
        conn, task["id"], owner="agent-1", config=config, run_checks=lambda cmd, cwd: True,
    )
    assert result["node"]["risk_tier"] == "low"
    assert result["auto_approved"] is True
    assert result["node"]["status"] == "done"


def test_rebuild_matches_live_through_light_mode_review_dispatch(conn, project, config):
    from muvue.core import rebuild

    manual = _ready_task(conn, project, config, criteria_mode="manual")
    nodes.start(conn, manual["id"], owner="a1")
    nodes.done(conn, manual["id"], owner="a1", config=config)

    external = _ready_task(conn, project, config, criteria_mode="external")
    nodes.start(conn, external["id"], owner="a2")
    nodes.done(conn, external["id"], owner="a2", config=config)

    assert rebuild.diff_state(conn) == {}
