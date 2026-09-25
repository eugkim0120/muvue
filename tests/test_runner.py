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
from muvue.core.config import AgentBudgetConfig, AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig

# `done` runs [checks] itself (v4 section 5); temp repos have no test
# suite, so the runner tests use commands that pass.
PASSING_CHECKS = ChecksConfig(test="true", lint="true")

FAKE_AGENT_AVAILABLE = shutil.which("muvue-fake-agent") is not None
pytestmark = pytest.mark.skipif(
    not FAKE_AGENT_AVAILABLE, reason="muvue-fake-agent console script not installed"
)


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig(
        checks=PASSING_CHECKS,
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
    # v4 section 2/6: every node_usage row also accrues to `agent_spend`,
    # in the unit this driver's cost_model ("tokens") actually produces.
    from muvue.core import spend as spend_mod

    total_tokens = conn.execute(
        "SELECT COALESCE(SUM(in_tokens + out_tokens), 0) c FROM node_usage"
    ).fetchone()["c"]
    assert spend_mod.get_spend(conn, project["id"], "fake", "tokens") == total_tokens
    assert total_tokens > 0


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
        checks=PASSING_CHECKS,
        agents={
            "fake": AgentConfig(
                command="MUVUE_FAKE_BEHAVIOR=rate_limited MUVUE_FAKE_RETRY_AFTER_SECONDS=3600 muvue-fake-agent",
                usage_parser="fake", cost_model="tokens", on_rate_limit="wait",
            ),
        },
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    from datetime import datetime, timedelta, timezone

    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    # retry_after (1h) is past max_wait_minutes (30), so the run sleeps
    # out the wait budget once, then escalates to the default "pause".
    result = runner_mod.run(
        db_path, cfg, repo_root, now_fn=lambda: clock["now"], sleep_fn=sleep,
    )
    assert clock["now"] == datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
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


# -- acceptance #6 (v4 section 2/6): PER-DRIVER budget stops the
# offending driver at 100%, warns at 80% -- there is no single global
# budget number any more (v4 changelog item 5; see docs/decisions.md for
# the tests this replaces, previously against the removed global
# `config.budget.unit`/`limit`). --------------------------------------


def test_budget_stops_runner_at_100_percent(conn, project, config, db_path, repo_root):
    tasks = _ready_tasks(conn, project, 3, predicted_touches=[])
    # cooperative fake usage is 1200 in + 400 out = 1600 tokens/node; a
    # limit under one node's worth trips "exhausted" after exactly one
    # node runs, before any further node is scheduled.
    config.agents["fake"].budget = AgentBudgetConfig(unit="tokens", limit=100)
    result = runner_mod.run(db_path, config, repo_root)
    # v4 section 6: an exhausted driver with no alternative route just
    # leaves nothing left to schedule -- not a distinct "paused" reason
    # (no separate "no path left" detection is built, per the plan).
    assert result["paused"] is None
    assert len(result["processed"]) == 1
    assert result["budget"]["fake"]["exhausted"] is True
    # the remaining nodes were never scheduled
    remaining = [
        nodes.get_node(conn, t["id"])["status"] for t in tasks
        if t["id"] != result["processed"][0]["node_id"]
    ]
    assert remaining == ["ready", "ready"]


def test_budget_stops_after_the_node_that_crosses_the_limit(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 3, predicted_touches=[])
    config.agents["fake"].budget = AgentBudgetConfig(unit="tokens", limit=1600)  # exactly one node's worth
    result = runner_mod.run(db_path, config, repo_root)
    assert len(result["processed"]) == 1
    assert result["budget"]["fake"]["exhausted"] is True


def test_budget_warns_at_80_percent(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 2, predicted_touches=[])
    config.agents["fake"].budget = AgentBudgetConfig(unit="tokens", limit=1700)  # one node (1600) is >= 80%
    runner_mod.run(db_path, config, repo_root)
    warnings = conn.execute(
        "SELECT * FROM events WHERE type = 'runner.driver_budget_warning'"
    ).fetchall()
    assert len(warnings) >= 1


def test_driver_with_no_budget_configured_is_never_exhausted(conn, project, config, db_path, repo_root):
    """`[agents.<x>.budget]` is optional -- no config means unlimited,
    never exhausted, never warned (v4 section 2)."""
    _ready_tasks(conn, project, 2, predicted_touches=[])
    result = runner_mod.run(db_path, config, repo_root)
    assert len(result["processed"]) == 2
    assert result["budget"] == {}


def test_one_driver_exhausted_other_driver_keeps_scheduling(conn, project, db_path, repo_root):
    """v4 section 6: "Runner stops the offending driver at 100% ...
    other drivers continue unless [routing] leaves no path." Two
    drivers, routing sends `task` kind to a tight-budget driver and
    `subtask` kind to a generous one; once the tight one is exhausted,
    its nodes stop getting scheduled while the generous one's nodes keep
    completing, and the run doesn't halt overall."""
    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
        agents={
            "tight": AgentConfig(
                command="muvue-fake-agent", usage_parser="fake", cost_model="tokens",
                budget=AgentBudgetConfig(unit="tokens", limit=1600),  # one node's worth
            ),
            "generous": AgentConfig(
                command="muvue-fake-agent", usage_parser="fake", cost_model="tokens",
                budget=AgentBudgetConfig(unit="tokens", limit=100_000),
            ),
        },
        routing=RoutingConfig(spec="tight", task="tight", subtask="generous"),
    )
    # 2 "task"-kind nodes routed to "tight" (only 1 will get a chance to
    # run before it's exhausted), 2 "subtask"-kind nodes routed to
    # "generous" (both should complete).
    for i in range(2):
        nodes.create_node(
            conn, project_id=project["id"], kind="task", title=f"tight{i}", status="ready",
            criteria_mode="auto", criteria=["ok"], predicted_touches=[],
        )
    for i in range(2):
        nodes.create_node(
            conn, project_id=project["id"], kind="subtask", title=f"gen{i}", status="ready",
            criteria_mode="auto", criteria=["ok"], predicted_touches=[],
        )
    result = runner_mod.run(db_path, cfg, repo_root)

    assert result["paused"] is None  # never halted -- "generous" always had routable work
    by_agent = {}
    for r in result["processed"]:
        by_agent.setdefault(r["agent"], 0)
        by_agent[r["agent"]] += 1
    assert by_agent.get("tight", 0) == 1  # stopped after exhausting its own budget
    assert by_agent.get("generous", 0) == 2  # both of its nodes completed regardless
    assert result["budget"]["tight"]["exhausted"] is True
    assert result["budget"]["generous"]["exhausted"] is False

    tight_nodes = conn.execute(
        "SELECT status FROM nodes WHERE title LIKE 'tight%'"
    ).fetchall()
    assert sorted(r["status"] for r in tight_nodes) == ["done", "ready"]
    gen_nodes = conn.execute("SELECT status FROM nodes WHERE title LIKE 'gen%'").fetchall()
    assert all(r["status"] == "done" for r in gen_nodes)


# -- v4 section 2 `[budget]`: unit-free max_wall_clock_minutes / -----------
# -- max_nodes_per_run stop conditions, independent of any driver budget ---


def test_max_nodes_per_run_stops_independent_of_driver_budget(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 3, predicted_touches=[])
    config.budget.max_nodes_per_run = 2
    result = runner_mod.run(db_path, config, repo_root)
    assert len(result["processed"]) == 2
    assert result["paused"]["reason"] == "max_nodes_per_run"


def test_max_wall_clock_minutes_stops_independent_of_driver_budget(conn, project, config, db_path, repo_root):
    """Injectable clock, not a real wall-clock sleep (matches the
    ask_timeout / idle-session-timeout convention)."""
    from datetime import datetime, timedelta, timezone

    _ready_tasks(conn, project, 3, predicted_touches=[])
    config.budget.max_wall_clock_minutes = 10

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls = {"n": 0}

    def fake_now():
        # `run` calls `now_fn()` twice before the loop body's own elapsed
        # check (once for reconcile_rate_limits, once for
        # run_started_at) -- both must still return `start` so the
        # baseline is real; the first in-loop elapsed check then jumps
        # forward far enough to be past max_wall_clock_minutes.
        calls["n"] += 1
        if calls["n"] <= 2:
            return start
        return start + timedelta(minutes=20)

    result = runner_mod.run(db_path, config, repo_root, now_fn=fake_now)
    assert result["paused"]["reason"] == "max_wall_clock_minutes"
    assert len(result["processed"]) < 3


# -- routing ([routing] kind -> agent) ---------------------------------------


def test_routing_picks_agent_by_node_kind(conn, project, db_path, repo_root):
    cfg = MuvueConfig(
        checks=PASSING_CHECKS,
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
        checks=PASSING_CHECKS,
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


def test_parallel_schedules_disjoint_touches_concurrently(conn, project, config, db_path, repo_root, monkeypatch):
    """v4 section 6 Delta C: --parallel > 1 only proceeds when
    worktree_mode = "per_node" -- this test's `config` fixture must opt
    in explicitly (the default is "branch", refused; see
    test_parallel_refused_outside_per_node below)."""
    from conftest import git_init_with_commit

    monkeypatch.setenv("HOME", str(repo_root))  # per_node worktrees live under ~/.muvue
    git_init_with_commit(repo_root)
    config.worktree_mode = "per_node"
    config.worktree_setup = ""
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


# -- v4 section 6 Delta C: --parallel N > 1 refused outside worktree_mode
# = "per_node" ---------------------------------------------------------


def test_parallel_refused_in_default_branch_mode(conn, project, config, db_path, repo_root):
    assert config.worktree_mode == "branch"  # the documented default (v4 section 2)
    nodes.create_node(
        conn, project_id=project["id"], kind="task", title="a", status="ready",
        predicted_touches=["a.py"],
    )
    with pytest.raises(runner_mod.ParallelismRefused):
        runner_mod.run(db_path, config, repo_root, parallel=2)
    # refused before doing anything -- the node was never touched
    assert nodes.get_node(conn, 1)["status"] == "ready"


def test_parallel_1_works_in_either_worktree_mode(conn, project, config, db_path, repo_root, monkeypatch):
    from conftest import git_init_with_commit

    monkeypatch.setenv("HOME", str(repo_root))  # per_node worktrees live under ~/.muvue
    git_init_with_commit(repo_root)
    config.worktree_setup = ""
    for mode in ("branch", "per_node"):
        config.worktree_mode = mode
        task = nodes.create_node(
            conn, project_id=project["id"], kind="task", title=f"t-{mode}", status="ready",
            criteria_mode="auto", criteria=["ok"],
        )
        result = runner_mod.run(db_path, config, repo_root, parallel=1)
        assert result["paused"] is None, result
        assert len(result["processed"]) == 1
        assert nodes.get_node(conn, task["id"])["status"] == "done"


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
        checks=PASSING_CHECKS,
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


def test_run_on_a_paused_project_stops_cleanly_without_starting(conn, project, config, db_path, repo_root):
    """Regression: `run_node` called `nodes.start` unguarded, so a paused
    project raised NodeError and crashed `muvue run`."""
    tasks = _ready_tasks(conn, project, 2, predicted_touches=[])
    projects.set_phase(conn, project["id"], "paused")
    result = runner_mod.run(db_path, config, repo_root, project_id=project["id"])
    assert result["paused"]["reason"] == "project_paused"
    assert result["processed"] == []
    for t in tasks:
        assert nodes.get_node(conn, t["id"])["status"] == "ready"


def test_run_without_project_filter_skips_paused_projects(conn, project, config, db_path, repo_root):
    _ready_tasks(conn, project, 1, predicted_touches=[])
    projects.set_phase(conn, project["id"], "paused")
    other = projects.set_phase(conn, projects.create_project(conn, goal="other")["id"], "executing")
    live = _ready_tasks(conn, other, 1, predicted_touches=[])
    result = runner_mod.run(db_path, config, repo_root)
    assert [r["node_id"] for r in result["processed"]] == [live[0]["id"]]


def test_pause_between_selection_and_start_is_a_clean_stop(conn, project, config, db_path, repo_root, monkeypatch):
    """A pause that lands after the batch was selected must not crash the
    run: `run_node` reports `project_paused` and the run stops."""
    tasks = _ready_tasks(conn, project, 1, predicted_touches=[])
    real_start = nodes.start

    def pause_then_start(c, node_id, **kw):
        projects.set_phase(c, project["id"], "paused")
        return real_start(c, node_id, **kw)

    monkeypatch.setattr(runner_mod.nodes_mod, "start", pause_then_start)
    result = runner_mod.run(db_path, config, repo_root, project_id=project["id"])
    assert result["processed"][0]["outcome"] == "project_paused"
    assert result["paused"]["reason"] == "node_blocked"
    assert nodes.get_node(conn, tasks[0]["id"])["status"] == "ready"


def test_a_node_preassigned_to_a_runner_routes_back_to_that_agent(conn, project, config):
    config.agents["other"] = config.agents["fake"].model_copy()
    node = nodes.create_node(
        conn, project_id=project["id"], kind="subtask", title="rebase", status="ready",
        owner="runner:other",
    )
    assert runner_mod.agent_for_node(node, config, None) == "other"
    assert runner_mod.agent_for_node(node, config, "fake") == "fake"
    plain = nodes.create_node(conn, project_id=project["id"], kind="subtask", title="x", status="ready")
    assert runner_mod.agent_for_node(plain, config, None) == config.routing.subtask


def test_a_failing_worktree_setup_stops_the_run_cleanly(conn, project, config, db_path, repo_root, monkeypatch):
    from conftest import git_init_with_commit

    monkeypatch.setenv("HOME", str(repo_root))
    git_init_with_commit(repo_root)
    config.worktree_mode = "per_node"
    config.worktree_setup = "exit 3"
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    result = runner_mod.run(db_path, config, repo_root)
    assert result["processed"][0]["outcome"] == "start_failed"
    assert "exit 3" in result["processed"][0]["error"]
    assert result["paused"]["reason"] == "node_blocked"
    assert nodes.get_node(conn, task["id"])["status"] == "ready"
