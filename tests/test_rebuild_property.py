"""P0 acceptance #2: replaying all `events` reproduces the live DB state.

Uses randomized sequences of core operations (create/start/done/fail) as a
lightweight property test (no extra dependency beyond the approved stack:
stdlib `random` drives the sequence generation).
"""

import random
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects, rebuild


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


def _executing_project(conn, goal: str):
    """P1 refuses `start` while project.phase == 'planning' (Gate 2 not yet
    approved). These P0-era property tests exercise the node lifecycle
    directly, so flip phase straight to 'executing' as if Gate 2 had
    already passed."""
    project = projects.create_project(conn, goal=goal)
    return projects.set_phase(conn, project["id"], "executing")


def test_rebuild_matches_live_after_scripted_sequence(conn):
    project = _executing_project(conn, "rebuild test project")
    n1 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t1", status="ready")
    n2 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t2", status="ready")
    n3 = nodes.create_node(conn, project_id=project["id"], kind="task", title="t3", status="ready")

    nodes.start(conn, n1["id"], owner="alice")
    nodes.done(conn, n1["id"], owner="alice")

    nodes.start(conn, n2["id"], owner="bob")
    nodes.fail(conn, n2["id"], owner="bob", lesson="flaky test", trigger="ci", do_instead="retry", scope="t2")

    nodes.start(conn, n3["id"], owner="carol", request_id="dup-1")
    nodes.start(conn, n3["id"], owner="carol", request_id="dup-1")  # duplicate, no-op

    assert rebuild.diff_state(conn) == {}


@pytest.mark.parametrize("seed", range(10))
def test_rebuild_matches_live_after_random_sequence(conn, seed):
    rng = random.Random(seed)
    project = _executing_project(conn, f"seed-{seed}")
    node_ids = [
        nodes.create_node(
            conn, project_id=project["id"], kind="task", title=f"n{i}", status="ready",
            max_attempts=2,
        )["id"]
        for i in range(5)
    ]
    owner = "agent-x"
    for node_id in node_ids:
        action = rng.choice(["start_done", "start_fail_fail", "start_only"])
        if action == "start_done":
            nodes.start(conn, node_id, owner=owner)
            nodes.done(conn, node_id, owner=owner)
            if rng.random() < 0.5:
                nodes.done(conn, node_id, owner=owner)  # redundant done, no-op
        elif action == "start_fail_fail":
            nodes.start(conn, node_id, owner=owner)
            nodes.fail(conn, node_id, owner=owner, lesson="l1", trigger="t", do_instead="d", scope="s")
            row = nodes.get_node(conn, node_id)
            if row["status"] == "ready":
                nodes.start(conn, node_id, owner=owner)
                nodes.fail(conn, node_id, owner=owner, lesson="l2", trigger="t", do_instead="d", scope="s")
        else:
            nodes.start(conn, node_id, owner=owner)

        assert rebuild.diff_state(conn) == {}, f"mismatch after acting on node {node_id}"

    assert rebuild.diff_state(conn) == {}


# --------------------------------------------------------------------------
# P5: runner/driver, merge-conflict, and handoff flows must replay too
# (working rule 1: write the rebuild test first for every new mutating
# flow -- every prior phase found at least one parity bug by skipping it).
# --------------------------------------------------------------------------


def _fake_config():
    from muvue.core.config import AgentConfig, MuvueConfig, RoutingConfig

    return MuvueConfig(
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens")},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )


def test_rebuild_matches_live_through_runner_done_flow(conn, tmp_path):
    from muvue.core import runner as runner_mod

    project = _executing_project(conn, "runner rebuild")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    config = _fake_config()
    agent_cfg = config.agents["fake"]
    row = nodes.get_node(conn, task["id"])
    runner_mod.run_node(conn, row, "fake", agent_cfg, config, tmp_path)
    assert rebuild.diff_state(conn) == {}


def test_rebuild_matches_live_through_runner_rate_limit_fallback(conn, tmp_path):
    from muvue.core.config import AgentConfig
    from muvue.core import runner as runner_mod

    project = _executing_project(conn, "runner rate limit rebuild")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    config = _fake_config()
    config.agents["claude"] = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=rate_limited muvue-fake-agent",
        usage_parser="fake", cost_model="tokens", on_rate_limit="fallback:fake",
    )
    row = nodes.get_node(conn, task["id"])
    runner_mod.run_node(conn, row, "claude", config.agents["claude"], config, tmp_path)
    assert rebuild.diff_state(conn) == {}


def test_rebuild_matches_live_through_merge_conflict_flow(tmp_path):
    """Real strict-mode git plumbing: two nodes bound to conflicting
    worktrees, one merged first, the second conflicts on merge."""
    import subprocess

    from muvue.core import db as core_db
    from muvue.core import gates, merge as merge_mod
    from muvue.core.config import MuvueConfig
    from muvue.core.repo_init import init_repo

    def _git(repo, *args):
        subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
            },
        )

    home = tmp_path / "home"
    home.mkdir()
    import os

    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        (repo / "shared.txt").write_text("base\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "initial")

        init_repo(repo)
        conn = core_db.connect(repo / ".muvue" / "muvue.db")
        cfg = MuvueConfig()
        cfg.mode = "strict"
        cfg.worktree_setup = ""

        project = projects.create_project(conn, goal="merge rebuild")
        n1 = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="t1", status="pending",
        )
        n2 = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="t2", status="pending",
        )
        gates.approve_gate2(conn, project["id"], config=cfg)

        started1 = nodes.start(conn, n1["id"], owner="a1", config=cfg, repo_root=repo)
        wt1 = Path(started1["node"]["worktree"])
        (wt1 / "shared.txt").write_text("from node 1\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "n1 change")
        nodes.done(conn, n1["id"], owner="a1")

        started2 = nodes.start(conn, n2["id"], owner="a2", config=cfg, repo_root=repo)
        wt2 = Path(started2["node"]["worktree"])
        (wt2 / "shared.txt").write_text("from node 2 (conflicting)\n")
        _git(wt2, "add", "-A")
        _git(wt2, "commit", "-q", "-m", "n2 change")
        nodes.done(conn, n2["id"], owner="a2")

        assert merge_mod.attempt_merge(conn, n1["id"], repo)["status"] == "merged"
        outcome = merge_mod.attempt_merge(conn, n2["id"], repo)
        assert outcome["status"] == "conflict"

        assert rebuild.diff_state(conn) == {}
        conn.close()
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)


def test_rebuild_matches_live_through_handoff(conn):
    project = _executing_project(conn, "handoff rebuild")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="agent-A")
    nodes.handoff(conn, task["id"], new_owner="human-jane")
    assert rebuild.diff_state(conn) == {}


# --------------------------------------------------------------------------
# v4 section 3: replay-scope redefinition. v3's P0 acceptance ("replay
# equals live DB") was false as written -- `lease_until` is wall-clock and
# can never be reproduced by replaying events recorded at a different
# real time. These two tests prove the *narrowed* comparison
# (`rebuild.diff_state`, now projecting both sides down to the
# replayable columns) still does its job: a genuine replayable-field
# discrepancy is still caught, and a discrepancy confined to a
# non-replayable field is no longer flagged.
# --------------------------------------------------------------------------


def test_diff_state_still_catches_a_genuine_replayable_discrepancy(conn):
    """A live-only mutation to a *replayable* column (here: `status`,
    written directly with no corresponding event -- simulating a bug
    where some future code path forgets to call through `core.nodes`)
    must still be caught. This is the "narrower comparison still catches
    real bugs" half of the v4 section 3 redefinition."""
    project = _executing_project(conn, "replay-scope genuine bug")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    assert rebuild.diff_state(conn) == {}

    # Simulate a hypothetical bug: a direct SQL write that bypasses
    # `core.nodes` and its event log entirely.
    conn.execute("UPDATE nodes SET status = 'blocked' WHERE id = ?", (node["id"],))
    conn.commit()

    mismatches = rebuild.diff_state(conn)
    assert "nodes" in mismatches
    assert node["id"] in mismatches["nodes"]
    assert mismatches["nodes"][node["id"]]["live"]["status"] == "blocked"
    assert mismatches["nodes"][node["id"]]["replayed"]["status"] == "ready"


def test_diff_state_does_not_flag_a_non_replayable_lease_until_difference(conn):
    """`lease_until` is wall-clock (v4 section 3: "Not replayable...
    lease_until"). Simulate real time having passed between the live
    snapshot and the replay by writing a `lease_until` value directly
    (no event, exactly as if the clock had simply moved on since the
    event carrying the original value was recorded) -- `diff_state` must
    NOT flag this, because `lease_until` is excluded from the equality
    check entirely, not merely tolerated by chance. This is the
    "non-replayable field difference no longer flagged" half of the v4
    section 3 redefinition; before this session's narrowing, this would
    have been (spuriously) reported as a mismatch."""
    project = _executing_project(conn, "replay-scope non-replayable field")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, node["id"], owner="agent-1", lease_minutes=60)
    assert rebuild.diff_state(conn) == {}

    # Simulate real time passing: the live row's lease_until now differs
    # from what the last node.start event's payload recorded, with no
    # new event at all (a plain clock tick, not a state change).
    conn.execute(
        "UPDATE nodes SET lease_until = '2099-01-01T00:00:00.000000Z' WHERE id = ?",
        (node["id"],),
    )
    conn.commit()

    assert rebuild.diff_state(conn) == {}, (
        "lease_until is non-replayable (v4 section 3) and must be excluded "
        "from the equality check, not merely happen to match"
    )
