"""P3 acceptance #4: scripted fake-agent behaviours (cooperative, lazy,
adversarial -- plan section 10) against muvue.core, asserting each
adversarial behaviour is blocked or flagged by core itself."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, rebuild
from muvue.core.config import MuvueConfig
from muvue.core.gates import HumanOnly
from muvue.core.state_machine import InvalidTransition, NotLeaseOwner

import fake_agent


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
    return projects.create_project(conn, goal="fake agent test")


def _ready_task(conn, project, config, **kw):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto",
        predicted_touches=["a.py"], status="pending", **kw,
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


# -- cooperative --------------------------------------------------------


def test_cooperative_run_completes_normally(conn, project, config):
    task = _ready_task(conn, project, config)
    result = fake_agent.cooperative_run(conn, task["id"])
    assert result["node"]["status"] == "done"


# -- lazy -----------------------------------------------------------------


def test_lazy_early_done_is_allowed_but_leaves_no_work_evidence(conn, project, config):
    """Documented P3 scope boundary (see fake_agent.py docstring): core
    has no mechanism yet to require evidence of work before `done`."""
    task = _ready_task(conn, project, config)
    result = fake_agent.lazy_early_done(conn, task["id"])
    assert result["node"]["status"] == "done"
    notes = conn.execute(
        "SELECT COUNT(*) c FROM notes WHERE node_id = ?", (task["id"],)
    ).fetchone()["c"]
    assert notes == 0


def test_lazy_vacuous_lesson_is_still_recorded_as_a_lesson_note(conn, project, config):
    task = _ready_task(conn, project, config)
    result = fake_agent.lazy_vacuous_lesson(conn, task["id"])
    assert result["node"]["status"] in ("ready", "failed")
    lessons = conn.execute(
        "SELECT * FROM notes WHERE node_id = ? AND kind = 'lesson'", (task["id"],)
    ).fetchall()
    assert len(lessons) == 1


# -- adversarial ------------------------------------------------------------


def test_adversarial_test_file_edit_is_flagged_never_auto_approved(conn, project, config):
    task = _ready_task(conn, project, config)
    result = fake_agent.adversarial_edit_test_file(conn, task["id"], config=config)
    assert result["auto_approved"] is False
    assert result["node"]["status"] == "review"


def test_adversarial_forged_trailer_does_not_grant_lease_or_move_status(conn, project, config):
    victim = _ready_task(conn, project, config)  # never started by the forger
    result = fake_agent.adversarial_forge_trailer(conn, forged_node_id=victim["id"])
    assert victim["id"] in result["linked_node_ids"]  # bookkeeping link is recorded...
    row = nodes.get_node(conn, victim["id"])
    assert row["status"] == "ready"  # ...but nothing about the node's lifecycle moved
    assert row["owner"] is None
    # the forger still cannot act on it without the lease -- the node
    # never left `ready`, so there isn't even a `review` edge to take
    # yet (InvalidTransition); a forger who raced a real `start` first
    # would instead hit NotLeaseOwner. Either way, `done` never succeeds.
    with pytest.raises((InvalidTransition, NotLeaseOwner)):
        nodes.done(conn, victim["id"], owner="forger")


def test_adversarial_call_human_verb_is_refused_by_core(conn, project):
    """core.gates itself refuses a non-human actor calling approve_node --
    not merely "the CLI/MCP surface never routes here"."""
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto", status="pending",
    )
    with pytest.raises(HumanOnly):
        fake_agent.adversarial_call_human_verb(conn, task["id"])
    assert nodes.get_node(conn, task["id"])["status"] == "pending"  # untouched


def test_adversarial_ignores_block_is_refused_by_state_machine(conn, project, config):
    task = _ready_task(conn, project, config)
    nodes.start(conn, task["id"], owner="fake-agent")
    nodes.block(conn, task["id"], reason="rate_limit", actor="fake-agent")
    assert nodes.get_node(conn, task["id"])["status"] == "blocked"
    with pytest.raises(InvalidTransition):
        fake_agent.adversarial_ignore_block(conn, task["id"], owner="fake-agent")
    assert nodes.get_node(conn, task["id"])["status"] == "blocked"  # still blocked, not done


def test_rebuild_matches_live_after_fake_agent_scenarios(conn, project, config):
    """Working rule: any new mutating flow must stay rebuild-replayable."""
    coop = _ready_task(conn, project, config)
    fake_agent.cooperative_run(conn, coop["id"])

    lazy = _ready_task(conn, project, config)
    fake_agent.lazy_early_done(conn, lazy["id"])

    vacuous = _ready_task(conn, project, config)
    fake_agent.lazy_vacuous_lesson(conn, vacuous["id"])

    flagged = _ready_task(conn, project, config)
    fake_agent.adversarial_edit_test_file(conn, flagged["id"], config=config)

    victim = _ready_task(conn, project, config)
    fake_agent.adversarial_forge_trailer(conn, forged_node_id=victim["id"])

    blocked = _ready_task(conn, project, config)
    nodes.start(conn, blocked["id"], owner="fake-agent")
    nodes.block(conn, blocked["id"], reason="rate_limit", actor="fake-agent")

    assert rebuild.diff_state(conn) == {}
