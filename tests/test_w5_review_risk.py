"""v4 section 5 review/risk deltas (W5): muvue runs auto checks itself,
outside the write lock; lint too; test-file flag from real touches; diff
size from real diff lines; deletions; structure signals; rubber-stamp on
medium/high approvals only; replan scope limits."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, hooks, nodes, projects, review, revisions, risk
from muvue.core.config import ChecksConfig, MuvueConfig, PlanningConfig


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig(checks=ChecksConfig(test="true", lint=""))


def _started(conn, config, *, mode="auto", touches=("a.py",), title="t"):
    project = projects.create_project(conn, goal="w5")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title=title,
        criteria=["passes"], criteria_mode=mode, predicted_touches=list(touches),
        status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    nodes.start(conn, task["id"], owner="agent-1")
    return nodes.get_node(conn, task["id"])


# -- checks -------------------------------------------------------------


def test_checks_run_outside_the_write_lock(conn, config, tmp_path):
    task = _started(conn, config)
    db_path = tmp_path / "muvue.db"
    lock_free = []

    def run_checks(cmd, cwd):
        other = sqlite3.connect(db_path, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
            lock_free.append(True)
        except sqlite3.OperationalError:
            lock_free.append(False)
        finally:
            other.close()
        return True

    nodes.done(conn, task["id"], owner="agent-1", config=config, run_checks=run_checks)
    assert lock_free == [True]


def test_lint_failure_flags_review_with_the_lint_command(conn):
    config = MuvueConfig(checks=ChecksConfig(test="true", lint="ruff check ."))
    task = _started(conn, config)
    result = nodes.done(
        conn, task["id"], owner="agent-1", config=config,
        run_checks=lambda cmd, cwd: cmd != "ruff check .",
    )
    assert result["node"]["status"] == "review"
    flagged = core_db.query_one(
        conn, "SELECT payload FROM events WHERE type = 'review.auto_check_failed'"
    )
    assert json.loads(flagged["payload"])["command"] == "ruff check ."


def test_empty_check_commands_are_skipped(conn):
    config = MuvueConfig(checks=ChecksConfig(test="", lint=""))
    task = _started(conn, config)
    ran = []
    nodes.done(conn, task["id"], owner="agent-1", config=config,
               run_checks=lambda cmd, cwd: ran.append(cmd) or False)
    assert ran == []


def test_mcp_done_runs_checks(tmp_path):
    from muvue.core.repo_init import init_repo
    from muvue.mcp_server import handle_request

    init_repo(tmp_path)
    config = MuvueConfig(checks=ChecksConfig(test="false", lint=""))
    c = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        task = _started(c, config)
    finally:
        c.close()
    resp = handle_request(tmp_path, config, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "done", "arguments": {"node_id": task["id"], "owner": "agent-1"}},
    })
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["node"]["status"] == "review"


# -- external criteria surfaced as unverified ----------------------------


def test_external_criteria_show_as_unverified(conn, config):
    task = _started(conn, config, mode="external")
    nodes.done(conn, task["id"], owner="agent-1", config=config)
    from muvue.core import queries

    shown = queries.show_node(conn, task["id"])
    assert shown["verification"] == "unverified"
    items = queries.unverified_external(conn)
    assert [i["node_id"] for i in items] == [task["id"]]


# -- test-file flag uses real touches ------------------------------------


def test_actual_test_touch_flags_even_if_not_predicted(conn, config):
    # "*.py" covers the test file too, so touches-outside-predicted
    # can't be what flags it; is_test_touch("*.py") is False.
    task = _started(conn, config, touches=("src/*", "*.py"))
    hooks.handle_post_commit(
        conn, commit_sha="abc123", message=f"work\n\nMuvue-Node: {task['id']}",
        files=["src/a.py", "tests/test_a.py"],
    )
    result = nodes.done(conn, task["id"], owner="agent-1", config=config)
    assert result["node"]["status"] == "review"


# -- diff size / deletions from git ---------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "g"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "old.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_diff_stats_counts_lines_and_deletions(git_repo):
    (git_repo / "big.py").write_text("".join(f"v{i} = {i}\n" for i in range(50)))
    (git_repo / "old.py").unlink()
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "change")
    sha = _git(git_repo, "rev-parse", "HEAD")
    stats = risk.diff_stats(git_repo, [sha])
    assert stats == {"lines": 51, "deleted": ["old.py"]}


def test_diff_stats_is_none_outside_git(tmp_path):
    assert risk.diff_stats(tmp_path, ["deadbeef"]) is None


def test_compute_tier_uses_real_diff_lines(conn, config):
    task = _started(conn, config)
    assert risk.compute_tier(conn, task, config, diff_lines=10) == "low"
    assert risk.compute_tier(conn, task, config, diff_lines=config.risk.max_diff_lines + 1) == "high"


def test_done_with_a_big_real_diff_goes_high(conn, config, git_repo):
    task = _started(conn, config, touches=("big.py",))
    (git_repo / "big.py").write_text("".join(f"v{i} = {i}\n" for i in range(400)))
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", f"big\n\nMuvue-Node: {task['id']}")
    hooks.handle_post_commit(
        conn, commit_sha=_git(git_repo, "rev-parse", "HEAD"),
        message=f"big\n\nMuvue-Node: {task['id']}", files=["big.py"],
    )
    result = nodes.done(conn, task["id"], owner="agent-1", config=config, cwd=str(git_repo))
    assert result["node"]["risk_tier"] == "high"
    assert result["node"]["status"] == "review"


def test_done_with_a_deletion_goes_high(conn, config, git_repo):
    task = _started(conn, config, touches=("old.py",))
    (git_repo / "old.py").unlink()
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "rm")
    hooks.handle_post_commit(
        conn, commit_sha=_git(git_repo, "rev-parse", "HEAD"),
        message=f"rm\n\nMuvue-Node: {task['id']}", files=["old.py"],
    )
    result = nodes.done(conn, task["id"], owner="agent-1", config=config, cwd=str(git_repo))
    assert result["node"]["risk_tier"] == "high"


# -- structure signals -----------------------------------------------------


def _component(conn, name, path, *, status="current"):
    with core_db.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO components (name, anchors_json, status) VALUES (?, ?, ?)",
            (name, json.dumps({path: "h"}), status),
        )
    return cur.lastrowid


def test_touching_a_component_with_invariants_is_high(conn, config):
    cid = _component(conn, "auth", "auth.py")
    with core_db.write_txn(conn):
        conn.execute("INSERT INTO invariants (text, component_id) VALUES (?, ?)", ("no plaintext", cid))
    task = _started(conn, config, touches=("auth.py",))
    assert risk.compute_tier(conn, task, config) == "high"


def test_touching_a_deprecated_component_is_at_least_medium(conn, config):
    _component(conn, "legacy", "legacy.py", status="deprecated")
    task = _started(conn, config, touches=("legacy.py",))
    assert risk.compute_tier(conn, task, config) == "medium"


# -- rubber stamp: medium/high only, on every approval kind ---------------


def test_fast_review_approval_of_low_tier_is_not_a_rubber_stamp(conn, config):
    task = _started(conn, config, mode="manual")
    nodes.done(conn, task["id"], owner="agent-1", config=config)
    nodes.approve_review(conn, task["id"])
    assert core_db.query_one(
        conn, "SELECT COUNT(*) c FROM events WHERE type = 'metric.rubber_stamp'"
    )["c"] == 0


def test_fast_gate_reapproval_of_high_tier_is_a_rubber_stamp(conn, config):
    task = _started(conn, config)
    gates.edit_criteria(conn, task["id"], criteria_json='["passes", "and more"]')
    gates.approve_node(conn, task["id"], config=config)
    stamps = core_db.query_all(conn, "SELECT payload FROM events WHERE type = 'metric.rubber_stamp'")
    assert len(stamps) == 1
    payload = json.loads(stamps[0]["payload"])
    assert payload["tier"] == "high"
    assert payload["approval"] == "node"
    assert core_db.query_one(
        conn, "SELECT COUNT(*) c FROM events WHERE type = 'metric.approval_timed'"
    )["c"] == 1


# -- replan scope ----------------------------------------------------------


def test_replan_subtask_inside_parent_scope_is_ready(conn, config):
    task = _started(conn, config, touches=("src/*",))
    sub = revisions.replan_add_subtask(
        conn, parent_task_id=task["id"], title="s", predicted_touches=["src/b.py"], config=config,
    )
    assert sub["status"] == "ready"


def test_replan_subtask_outside_parent_scope_is_gated(conn, config):
    task = _started(conn, config, touches=("src/*",))
    sub = revisions.replan_add_subtask(
        conn, parent_task_id=task["id"], title="s", predicted_touches=["migrations/x.sql"],
        config=config,
    )
    assert sub["status"] == "pending"
    gated = core_db.query_one(conn, "SELECT payload FROM events WHERE type = 'replan.gated'")
    assert "outside" in json.loads(gated["payload"])["reason"]


def test_replan_past_max_subtasks_is_gated(conn):
    config = MuvueConfig(checks=ChecksConfig(test="true", lint=""),
                         planning=PlanningConfig(max_subtasks=1))
    task = _started(conn, config)
    first = revisions.replan_add_subtask(conn, parent_task_id=task["id"], title="a", config=config)
    second = revisions.replan_add_subtask(conn, parent_task_id=task["id"], title="b", config=config)
    assert first["status"] == "ready"
    assert second["status"] == "pending"
