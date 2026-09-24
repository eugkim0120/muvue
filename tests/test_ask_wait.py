"""P1 acceptance #4: unanswered `ask` blocks unless `--default-ok`."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from muvue.core import asks, db as core_db, gates, nodes, projects
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
def in_progress_task(conn, config):
    project = projects.create_project(conn, goal="ask/wait")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="risky thing",
        criteria=["works"], criteria_mode="auto", predicted_touches=["a.py"],
        status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    nodes.start(conn, task["id"], owner="agent-1")
    return nodes.get_node(conn, task["id"])


def test_ask_creates_open_question(conn, in_progress_task):
    result = asks.ask(
        conn, in_progress_task["id"], question="ok to drop legacy field?", default="yes"
    )
    assert result["noop"] is False
    assert result["question"]["status"] == "open"
    assert result["question"]["default_answer"] == "yes"


def test_wait_before_timeout_stays_pending(conn, in_progress_task, config):
    q = asks.ask(conn, in_progress_task["id"], question="q?", default="yes")["question"]
    result = asks.wait(
        conn, q["id"], timeout_minutes=config.planning.ask_timeout_minutes,
        now=datetime.now(timezone.utc),
    )
    assert result["status"] == "pending"
    assert nodes.get_node(conn, in_progress_task["id"])["status"] == "in_progress"


def test_unanswered_ask_blocks_node_past_timeout_without_default_ok(
    conn, in_progress_task, config
):
    q = asks.ask(conn, in_progress_task["id"], question="q?", default="yes")["question"]
    past_deadline = datetime.now(timezone.utc) + timedelta(
        minutes=config.planning.ask_timeout_minutes + 1
    )
    result = asks.wait(
        conn, q["id"], timeout_minutes=config.planning.ask_timeout_minutes,
        default_ok=False, now=past_deadline,
    )
    assert result["status"] == "blocked"
    node = nodes.get_node(conn, in_progress_task["id"])
    assert node["status"] == "blocked"
    assert node["block_reason"] == "question"


def test_unanswered_ask_proceeds_with_default_when_default_ok(
    conn, in_progress_task, config
):
    q = asks.ask(conn, in_progress_task["id"], question="q?", default="yes")["question"]
    past_deadline = datetime.now(timezone.utc) + timedelta(
        minutes=config.planning.ask_timeout_minutes + 1
    )
    result = asks.wait(
        conn, q["id"], timeout_minutes=config.planning.ask_timeout_minutes,
        default_ok=True, now=past_deadline,
    )
    assert result["status"] == "default_applied"
    assert result["answer"] == "yes"
    node = nodes.get_node(conn, in_progress_task["id"])
    assert node["status"] == "in_progress"  # proceeds, never blocked
    notes = conn.execute(
        "SELECT * FROM notes WHERE node_id = ? AND kind = 'feedback'", (node["id"],)
    ).fetchall()
    assert any(n["text"] == "yes" for n in notes)


def test_answered_ask_never_blocks_even_past_timeout(conn, in_progress_task, config):
    q = asks.ask(conn, in_progress_task["id"], question="q?", default="yes")["question"]
    asks.answer(conn, q["id"], text="no, keep it")
    past_deadline = datetime.now(timezone.utc) + timedelta(
        minutes=config.planning.ask_timeout_minutes + 1
    )
    result = asks.wait(
        conn, q["id"], timeout_minutes=config.planning.ask_timeout_minutes,
        default_ok=False, now=past_deadline,
    )
    assert result["status"] == "answered"
    assert result["answer"] == "no, keep it"
    assert nodes.get_node(conn, in_progress_task["id"])["status"] == "in_progress"
