"""P1 acceptance #4: unanswered `ask` blocks unless `--default-ok`."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from muvue.core import asks, db as core_db, gates, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


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


# -- `answer` human verb (docs/protocol.md gap: no entry point existed) ----


def test_answer_rejects_non_human_actor(conn, in_progress_task):
    q = asks.ask(conn, in_progress_task["id"], question="q?", default="yes")["question"]
    with pytest.raises(asks.HumanOnly):
        asks.answer(conn, q["id"], text="no", actor="agent")
    assert asks.get_question(conn, q["id"])["status"] == "open"


def test_answer_via_real_cli(conn, in_progress_task, tmp_path: Path):
    """Proves `muvue answer` (human verb, never MCP -- plan section 4)
    reaches core.asks.answer through the real CLI, not just core."""
    repo_root = tmp_path
    init_repo(repo_root)
    db_path = repo_root / ".muvue" / "muvue.db"
    repo_conn = core_db.connect(db_path)
    project = projects.create_project(repo_conn, goal="answer cli test")
    task = nodes.create_node(
        repo_conn, project_id=project["id"], kind="task", title="t",
        criteria=["works"], criteria_mode="auto", predicted_touches=["a.py"],
        status="pending",
    )
    gates.approve_gate2(repo_conn, project["id"], config=MuvueConfig())
    nodes.start(repo_conn, task["id"], owner="agent-1")
    q = asks.ask(repo_conn, task["id"], question="ok?", default="yes")["question"]
    repo_conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "muvue", "answer", str(q["id"]), "--text", "yes, proceed",
         "--path", str(repo_root)],
        cwd=repo_root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["question"]["status"] == "answered"
    assert payload["question"]["answer"] == "yes, proceed"


# -- v4 section 4: `ask --default TEXT --default-ok` ------------------------


def test_ask_requires_a_proposed_default(conn, in_progress_task):
    with pytest.raises(asks.AskError, match="default"):
        asks.ask(conn, in_progress_task["id"], question="q?", default="")


def test_default_ok_set_at_ask_time_applies_default_on_timeout(conn, in_progress_task, config):
    q = asks.ask(
        conn, in_progress_task["id"], question="q?", default="yes", default_ok=True
    )["question"]
    assert q["default_ok"] == 1
    past_deadline = datetime.now(timezone.utc) + timedelta(
        minutes=config.planning.ask_timeout_minutes + 1
    )
    result = asks.wait(
        conn, q["id"], timeout_minutes=config.planning.ask_timeout_minutes, now=past_deadline
    )
    assert result == {"status": "default_applied", "answer": "yes"}
    assert nodes.get_node(conn, in_progress_task["id"])["status"] == "in_progress"


def test_cli_ask_default_is_required_and_default_ok_is_stored(tmp_path: Path):
    init_repo(tmp_path)
    repo_conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    project = projects.create_project(repo_conn, goal="cli ask")
    task = nodes.create_node(
        repo_conn, project_id=project["id"], kind="task", title="t",
        criteria=["works"], criteria_mode="auto", status="pending",
    )
    gates.approve_gate2(repo_conn, project["id"], config=MuvueConfig())
    repo_conn.close()

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "muvue", *args, "--path", str(tmp_path)],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )

    missing = cli("ask", str(task["id"]), "--question", "q?")
    assert missing.returncode != 0
    from conftest import plain

    assert "--default" in plain(missing.stderr)
    ok = cli("ask", str(task["id"]), "--question", "q?", "--default", "yes", "--default-ok")
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["question"]["default_ok"] == 1
