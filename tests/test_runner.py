"""P5 acceptance tests (plan section 11 row P5), runner half.

Constraint (per the P5 prompt): this environment has no network access
and no real Claude/Codex/Gemini CLI login available, so acceptance
criteria 1, 2, 4, 6 are satisfied using `config.agents.fake`
(`muvue-fake-agent`, a real subprocess -- see `src/muvue/fake_agent.py`)
as the stand-in for a real subscription-authenticated vendor CLI. The
`claude`/`codex`/`gemini` driver config parsing and usage-parser dispatch
are unit-tested separately (`tests/test_drivers.py`) against synthetic
recorded-output samples, since no real vendor CLI is reachable here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, nodes, projects, runner as runner_mod
from muvue.core.config import AgentConfig, MuvueConfig, RoutingConfig

FAKE_AGENT_AVAILABLE = shutil.which("muvue-fake-agent") is not None
pytestmark = pytest.mark.skipif(
    not FAKE_AGENT_AVAILABLE, reason="muvue-fake-agent console script not installed"
)


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig(
        agents={
            "fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
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
    p = projects.create_project(conn, goal="p5 runner test")
    return projects.set_phase(conn, p["id"], "executing")


def _ready_tasks(conn, project, n, **kw):
    kw.setdefault("criteria_mode", "auto")
    kw.setdefault("criteria", ["ok"])
    return [
        nodes.create_node(
            conn, project_id=project["id"], kind="task", title=f"t{i}",
            status="ready", **kw,
        )
        for i in range(n)
    ]


# -- acceptance #1: 5 tasks unattended on a (fake, substituting for a real
#    subscription-authenticated) CLI ------------------------------------


def test_five_tasks_run_unattended_end_to_end(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 5, predicted_touches=[])
    result = runner_mod.run(db_path, config, repo_root)
    assert len(result["processed"]) == 5
    assert all(r["outcome"] in ("done", "review") for r in result["processed"])
    assert result["paused"] is None or result["paused"]["reason"] != "budget_exhausted"

    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM nodes").fetchall()
    }
    assert all(s == "done" for s in statuses.values())
    # real usage rows recorded per node (plan section 6: "usage recorded to node_usage")
    usage_rows = conn.execute("SELECT COUNT(*) c FROM node_usage").fetchone()["c"]
    assert usage_rows == 5


def test_run_spawns_a_fresh_subprocess_per_node_not_shared_state(conn, project, config, db_path, repo_root):
    """No shared process state between nodes (plan section 1 principle 1,
    section 6: "fresh context per spawn")."""
    calls = []
    from muvue.core import drivers

    def counting_invoke(agent_name, agent_cfg, brief_text, cwd, **kw):
        calls.append(cwd)
        return drivers.invoke_driver(agent_name, agent_cfg, brief_text, cwd, **kw)

    _ready_tasks(conn, project, 3, predicted_touches=[])
    runner_mod.run(db_path, config, repo_root, invoke=counting_invoke)
    assert len(calls) == 3


# -- acceptance #2: pause on approval/blocked/failed/awaiting_approval ------


def test_run_pauses_without_scheduling_when_a_node_needs_attention(conn, project, config, db_path, repo_root):
    ready = _ready_tasks(conn, project, 2, predicted_touches=[])
    stuck = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="stuck", status="ready",
    )
    nodes.start(conn, stuck["id"], owner="someone")
    nodes.block(conn, stuck["id"], reason="question", actor="someone")

    result = runner_mod.run(db_path, config, repo_root)
    assert result["paused"]["reason"] == "nodes_need_attention"
    assert result["processed"] == []  # never crashed, just returned control
    # the two otherwise-ready nodes are untouched
    for t in ready:
        assert nodes.get_node(conn, t["id"])["status"] == "ready"


def test_run_pauses_after_a_node_it_just_processed_lands_in_failed(conn, project, config, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=1,
    )
    failing_cfg = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=failed muvue-fake-agent", usage_parser="fake", cost_model="tokens",
    )
    config.agents["fake"] = failing_cfg
    result = runner_mod.run(db_path, config, repo_root)
    assert result["paused"]["reason"] == "node_blocked"
    assert "failed" in result["paused"]["outcomes"]
    assert nodes.get_node(conn, task["id"])["status"] == "failed"


# -- acceptance #3: handoff resumes interactively from DB state -------------


def test_handoff_lets_a_human_resume_after_a_pause(conn, project, config, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="runner:fake")
    handed = nodes.handoff(conn, task["id"], new_owner="human-jane")
    assert handed["node"]["owner"] == "human-jane"
    assert handed["node"]["status"] == "in_progress"
    # the human can now finish it purely via the normal `done` verb --
    # no in-memory runner state was needed to resume.
    finished = nodes.done(conn, task["id"], owner="human-jane")
    assert finished["node"]["status"] == "done"


def test_handoff_unblocks_a_blocked_node_for_a_new_owner(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="runner:claude")
    nodes.block(conn, task["id"], reason="question", actor="runner:claude")
    handed = nodes.handoff(conn, task["id"], new_owner="human-jane")
    assert handed["node"]["status"] == "in_progress"
    assert handed["node"]["owner"] == "human-jane"


# -- acceptance #4: simulated rate limit -> configured fallback -------------


def test_rate_limit_triggers_configured_fallback_agent(conn, project, config, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria_mode="auto", criteria=["ok"],
    )
    config.agents["claude"] = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=rate_limited muvue-fake-agent",
        usage_parser="fake", cost_model="tokens", on_rate_limit="fallback:fake",
    )
    result = runner_mod.run(db_path, config, repo_root, agent_override="claude")
    assert len(result["processed"]) == 1
    outcome = result["processed"][0]
    assert outcome["fallback_from"] == "claude"
    assert outcome["agent"] == "fake"
    assert outcome["outcome"] == "done"

    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "done"
    # rate-limited usage from claude was still recorded before falling back
    rows = [dict(r) for r in conn.execute("SELECT * FROM node_usage ORDER BY id").fetchall()]
    assert rows[0]["agent"] == "claude"
    assert rows[0]["rate_limited"] == 1
    assert rows[-1]["agent"] == "fake"

    # and a runner.rate_limited event with retry_at was recorded (P5 spec:
    # "node -> blocked(rate_limit) with retry_at")
    events = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.rate_limited'"
    ).fetchall()
    assert len(events) == 1


def test_rate_limit_wait_mode_blocks_without_fallback(conn, project, db_path, repo_root):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    cfg = MuvueConfig(
        agents={
            "fake": AgentConfig(
                command="MUVUE_FAKE_BEHAVIOR=rate_limited muvue-fake-agent",
                usage_parser="fake", cost_model="tokens", on_rate_limit="wait",
            ),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    result = runner_mod.run(db_path, cfg, repo_root)
    assert result["paused"]["reason"] == "node_blocked"
    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "blocked"
    assert row["block_reason"] == "rate_limit"


def test_reconcile_rate_limits_unblocks_once_retry_at_has_passed(conn, project, config, db_path, repo_root):
    from datetime import datetime, timedelta, timezone

    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="runner:fake")
    nodes.block(conn, task["id"], reason="rate_limit", actor="runner:fake")
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    from muvue.core import events as events_mod

    events_mod.record_event(
        conn, project_id=project["id"], node_id=task["id"], actor="daemon",
        type_="runner.rate_limited", payload={"agent": "fake", "retry_at": past},
    )
    conn.commit()
    unblocked = runner_mod.reconcile_rate_limits(conn)
    assert unblocked == [task["id"]]
    assert nodes.get_node(conn, task["id"])["status"] == "ready"


# -- acceptance #6: budget stops the runner at 100%, warns at 80% -----------


def test_budget_stops_runner_at_100_percent(conn, project, config, db_path, repo_root):
    tasks = _ready_tasks(conn, project, 3, predicted_touches=[])
    # cooperative fake usage is 1200 in + 400 out = 1600 tokens/node; a
    # limit under one node's worth trips "exhausted" after exactly one
    # node runs, before any further node is scheduled.
    config.budget.unit = "tokens"
    config.budget.limit = 100
    result = runner_mod.run(db_path, config, repo_root)
    assert result["paused"]["reason"] == "budget_exhausted"
    assert len(result["processed"]) == 1
    assert result["budget"]["exhausted"] is True
    # the remaining nodes were never scheduled
    remaining = [
        nodes.get_node(conn, t["id"])["status"] for t in tasks
        if t["id"] != result["processed"][0]["node_id"]
    ]
    assert remaining == ["ready", "ready"]


def test_budget_stops_after_the_node_that_crosses_the_limit(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 3, predicted_touches=[])
    config.budget.unit = "tokens"
    config.budget.limit = 1600  # exactly one cooperative node's worth
    result = runner_mod.run(db_path, config, repo_root)
    assert len(result["processed"]) == 1
    assert result["paused"]["reason"] == "budget_exhausted"


def test_budget_warns_at_80_percent(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 2, predicted_touches=[])
    config.budget.unit = "tokens"
    config.budget.limit = 1700  # one node (1600) is >= 80% of 1700
    runner_mod.run(db_path, config, repo_root)
    warnings = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.budget_warning'"
    ).fetchall()
    assert len(warnings) >= 1


# -- routing ([routing] kind -> agent) ---------------------------------------


def test_routing_picks_agent_by_node_kind(conn, project, db_path, repo_root):
    cfg = MuvueConfig(
        agents={
            "fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
            "other": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
        },
        routing=RoutingConfig(spec="other", task="fake", subtask="other"),
    )
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    result = runner_mod.run(db_path, cfg, repo_root)
    assert result["processed"][0]["agent"] == "fake"


def test_agent_override_beats_routing(conn, project, db_path, repo_root):
    cfg = MuvueConfig(
        agents={
            "fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
            "other": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens"),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    result = runner_mod.run(db_path, cfg, repo_root, agent_override="other")
    assert result["processed"][0]["agent"] == "other"


# -- --parallel N: real disjoint-touch scheduling ----------------------------


def test_parallel_schedules_disjoint_touches_concurrently(conn, project, config, db_path, repo_root):
    config.agents["fake"].max_concurrency = 5
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="a", status="ready",
        predicted_touches=["a.py"],
    )
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="b", status="ready",
        predicted_touches=["b.py"],
    )
    result = runner_mod.run(db_path, config, repo_root, parallel=2)
    assert len(result["processed"]) == 2
    assert result["cycles"] == 1  # both scheduled in the same cycle


def test_parallel_serializes_overlapping_touches(conn):
    """Two ready nodes touching the same file are never selected into the
    same batch, even with --parallel 2 (real disjoint-set scheduling, not
    fake concurrency)."""
    from muvue.core.config import MuvueConfig as Cfg

    project = projects.create_project(conn, goal="overlap test")
    projects.set_phase(conn, project["id"], "executing")
    cfg = Cfg(
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens", max_concurrency=5)},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="a", status="ready",
        predicted_touches=["shared.py"],
    )
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="b", status="ready",
        predicted_touches=["shared.py"],
    )
    ready_rows = conn.execute("SELECT * FROM nodes WHERE status = 'ready' ORDER BY id").fetchall()
    batch = runner_mod.select_batch(conn, ready_rows, cfg, parallel=2, agent_override=None)
    assert len(batch) == 1


def test_max_concurrency_caps_batch_per_agent(conn):
    project = projects.create_project(conn, goal="concurrency cap")
    projects.set_phase(conn, project["id"], "executing")
    cfg = MuvueConfig(
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens", max_concurrency=1)},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="a", status="ready", predicted_touches=["a.py"],
    )
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="b", status="ready", predicted_touches=["b.py"],
    )
    ready_rows = conn.execute("SELECT * FROM nodes WHERE status = 'ready' ORDER BY id").fetchall()
    batch = runner_mod.select_batch(conn, ready_rows, cfg, parallel=5, agent_override=None)
    assert len(batch) == 1  # capped by max_concurrency=1 even though parallel=5
