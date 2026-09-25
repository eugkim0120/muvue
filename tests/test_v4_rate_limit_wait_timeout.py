"""v4 section 6 / changelog item 11: `max_wait_minutes` bounds
`on_rate_limit = "wait"`. Past that, `on_rate_limit_timeout` applies and
a notification fires (`runner.rate_limit_wait_exhausted`, written via
`write_txn` -- the minimum acceptable "notification fires" per the P5
prompt, since no real notification-sending exists yet outside the
dashboard; see docs/decisions.md).

P5's own acceptance wording (plan section 11 P5 row, re-read for v4):
"simulated rate limit triggers configured fallback and `max_wait`
timeout escalates" -- both halves are proven here: the existing
fallback-on-rate-limit behavior (test_rate_limit_triggers_configured_
fallback_agent in tests/test_runner.py) still passes unmodified, and the
new timeout-escalation half is covered below, using an injectable clock
rather than a real 30-minute sleep (matching ask_timeout /
idle-session-timeout convention)."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects, runner as runner_mod
from muvue.core.config import AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig

# `done` runs [checks] itself (v4 section 5); temp repos have no test
# suite, so the runner tests use commands that pass.
PASSING_CHECKS = ChecksConfig(test="true", lint="true")

FAKE_AGENT_AVAILABLE = shutil.which("muvue-fake-agent") is not None
pytestmark = pytest.mark.skipif(
    not FAKE_AGENT_AVAILABLE, reason="muvue-fake-agent console script not installed"
)


@pytest.fixture
def repo_root(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "muvue.db"


@pytest.fixture
def conn(db_path):
    c = core_db.init_db(db_path)
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="rate limit wait timeout test")
    return projects.set_phase(conn, p["id"], "executing")


class FakeClock:
    """`now` plus a `sleep` that advances it, so a wait of minutes costs
    no real time."""

    def __init__(self, start: datetime):
        self.current = start
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += timedelta(seconds=max(0.0, seconds))


def test_rate_limit_wait_past_max_wait_minutes_escalates_to_pause(conn, project, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
        agents={
            "fake": AgentConfig(
                command="MUVUE_FAKE_BEHAVIOR=rate_limited MUVUE_FAKE_RETRY_AFTER_SECONDS=120 muvue-fake-agent",
                usage_parser="fake", cost_model="tokens",
                on_rate_limit="wait", max_wait_minutes=5, on_rate_limit_timeout="pause",
            ),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))

    result = runner_mod.run(db_path, cfg, repo_root, now_fn=clock.now, sleep_fn=clock.sleep)
    # Waited inside the run: 120s, 120s, then the last 60s of the 5-minute
    # budget, retrying in between, before escalating.
    assert clock.slept == [120.0, 120.0, 60.0]
    assert [r["outcome"] for r in result["processed"][:-1]] == ["rate_limit_waited"] * 2
    outcome = result["processed"][-1]
    assert outcome["rate_limit_timeout_escalated"] is True
    assert outcome["on_rate_limit_timeout"] == "pause"
    assert outcome["outcome"] == "blocked_rate_limit"

    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "blocked"
    assert row["block_reason"] == "rate_limit"

    notifications = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.rate_limit_wait_exhausted'"
    ).fetchall()
    assert len(notifications) == 1


def test_rate_limit_wait_past_max_wait_minutes_escalates_to_fallback(conn, project, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria_mode="auto", criteria=["ok"],
    )
    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
        agents={
            "claude": AgentConfig(
                command="MUVUE_FAKE_BEHAVIOR=rate_limited MUVUE_FAKE_RETRY_AFTER_SECONDS=600 muvue-fake-agent",
                usage_parser="fake", cost_model="tokens",
                on_rate_limit="wait", max_wait_minutes=5,
                on_rate_limit_timeout="fallback:fake",
            ),
            "fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
        },
        routing=RoutingConfig(spec="claude", task="claude", subtask="claude"),
    )
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))

    result = runner_mod.run(db_path, cfg, repo_root, now_fn=clock.now, sleep_fn=clock.sleep)
    assert clock.slept == [300.0]  # retry_after 600s > the 5-minute budget
    outcome = result["processed"][0]
    assert outcome["rate_limit_timeout_escalated"] is True
    assert outcome["fallback_from"] == "claude"
    assert outcome["agent"] == "fake"
    assert outcome["outcome"] == "done"

    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "done"

    notifications = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.rate_limit_wait_exhausted'"
    ).fetchall()
    assert len(notifications) == 1


def test_rate_limit_that_clears_before_max_wait_never_hits_timeout_path(conn, project, db_path, repo_root):
    """A node whose rate limit clears (retry_at passes) before
    max_wait_minutes elapses is retried normally -- reconcile_rate_limits
    un-blocks it -- without ever hitting the timeout-escalation path."""
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria_mode="auto", criteria=["ok"],
    )
    nodes.start(conn, task["id"], owner="runner:fake")
    nodes.block(conn, task["id"], reason="rate_limit", actor="runner:fake")
    from muvue.core import events as events_mod

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    events_mod.record_event(
        conn, project_id=project["id"], node_id=task["id"], actor="daemon",
        type_="runner.rate_limited",
        payload={
            "agent": "fake", "retry_at": past,
            "wait_started_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        },
    )
    conn.commit()

    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
        agents={
            "fake": AgentConfig(
                command="muvue-fake-agent", usage_parser="fake", cost_model="tokens",
                on_rate_limit="wait", max_wait_minutes=30,
            ),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    result = runner_mod.run(db_path, cfg, repo_root)
    assert len(result["processed"]) == 1
    outcome = result["processed"][0]
    assert outcome["outcome"] == "done"
    assert "rate_limit_timeout_escalated" not in outcome

    notifications = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.rate_limit_wait_exhausted'"
    ).fetchall()
    assert notifications == []


def test_wait_sleeps_inside_the_run_then_the_retry_succeeds(conn, project, db_path, repo_root):
    """v4 section 6: `on_rate_limit = "wait"` waits and retries within the
    same run instead of stopping it."""
    from muvue.core import drivers

    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria_mode="auto", criteria=["ok"],
    )
    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake",
                                    cost_model="tokens", on_rate_limit="wait", max_wait_minutes=30)},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    calls = []

    def once_limited(agent_name, agent_cfg, brief_text, cwd, **kw):
        calls.append(agent_name)
        if len(calls) == 1:
            return drivers.DriverResult(status="rate_limited", retry_after_seconds=90)
        return drivers.invoke_driver(agent_name, agent_cfg, brief_text, cwd, **kw)

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    result = runner_mod.run(
        db_path, cfg, repo_root, invoke=once_limited, now_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert clock.slept == [90.0]
    assert [r["outcome"] for r in result["processed"]] == ["rate_limit_waited", "done"]
    assert result["paused"] is None
    assert nodes.get_node(conn, task["id"])["status"] == "done"
