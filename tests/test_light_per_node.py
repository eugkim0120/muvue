"""v4 section 6: `worktree_mode = "per_node"` in light mode gives each
node its own git worktree (branch `node-<id>`, off the user's HEAD), so
`--parallel` agents never share a checkout. `merge` then merges the node
branch into the user's checkout, only when it is clean, in dependency
order, and `muvue run` does that automatically after each cycle."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, merge as merge_mod, nodes, projects, runner as runner_mod, strict
from muvue.core.config import AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig
from muvue.core.repo_init import init_repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def repo(tmp_path, home) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("x = 1\n")
    init_repo(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig(
        worktree_mode="per_node", worktree_setup="",
        checks=ChecksConfig(test="true", lint="true"),
        agents={"fake": AgentConfig(command="muvue-fake-agent", usage_parser="fake",
                                    cost_model="tokens", max_concurrency=4)},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _task(conn, config, title, touches, depends_on=None):
    project = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    pid = project["id"] if project else projects.create_project(conn, goal="g")["id"]
    node = nodes.create_node(
        conn, project_id=pid, kind="task", title=title, criteria=["ok"], criteria_mode="auto",
        predicted_touches=touches, status="pending", depends_on=depends_on,
    )
    return node


def _approve_all(conn, config):
    pid = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()["id"]
    gates.approve_gate2(conn, pid, config=config)


def _work(conn, config, repo, node, filename, content):
    started = nodes.start(conn, node["id"], owner="agent-1", config=config, repo_root=repo)["node"]
    wt = Path(started["worktree"])
    (wt / filename).write_text(content)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"work\n\nMuvue-Node: {node['id']}")
    nodes.done(conn, node["id"], owner="agent-1", config=None)
    return wt


def test_start_binds_a_per_node_worktree_off_head_in_light_mode(conn, config, repo):
    node = _task(conn, config, "a", ["a.py"])
    _approve_all(conn, config)
    started = nodes.start(conn, node["id"], owner="agent-1", config=config, repo_root=repo)["node"]
    wt = Path(started["worktree"])
    assert wt == strict.worktrees_root(repo) / f"node-{node['id']}"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == f"node-{node['id']}"
    assert _git(wt, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")
    # the user's checkout stays on its own branch
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_branch_mode_light_start_binds_nothing(conn, config, repo):
    config.worktree_mode = "branch"
    node = _task(conn, config, "a", ["a.py"])
    _approve_all(conn, config)
    started = nodes.start(conn, node["id"], owner="agent-1", config=config, repo_root=repo)["node"]
    assert started["worktree"] is None


def test_merge_brings_the_node_branch_into_a_clean_checkout(conn, config, repo):
    node = _task(conn, config, "a", ["a.py"])
    _approve_all(conn, config)
    _work(conn, config, repo, node, "a.py", "a = 1\n")
    result = merge_mod.attempt_merge(conn, node["id"], repo)
    assert result["status"] == "merged"
    assert (repo / "a.py").read_text() == "a = 1\n"
    assert _git(repo, "rev-parse", "HEAD") == result["sha"]
    assert merge_mod.attempt_merge(conn, node["id"], repo)["status"] == "already_merged"


def test_merge_waits_for_a_dirty_checkout(conn, config, repo):
    node = _task(conn, config, "a", ["a.py"])
    _approve_all(conn, config)
    _work(conn, config, repo, node, "a.py", "a = 1\n")
    (repo / "app.py").write_text("x = 2  # user edit in progress\n")
    result = merge_mod.attempt_merge(conn, node["id"], repo)
    assert result == {"status": "deferred", "reason": "dirty_checkout"}
    assert (repo / "app.py").read_text() == "x = 2  # user edit in progress\n"
    assert not (repo / "a.py").exists()


def test_light_merge_conflict_blocks_and_creates_rebase_subtask(conn, config, repo):
    a = _task(conn, config, "a", ["app.py"])
    b = _task(conn, config, "b", ["app.py"])
    _approve_all(conn, config)
    _work(conn, config, repo, a, "app.py", "x = 'a'\n")
    _work(conn, config, repo, b, "app.py", "x = 'b'\n")
    assert merge_mod.attempt_merge(conn, a["id"], repo)["status"] == "merged"
    result = merge_mod.attempt_merge(conn, b["id"], repo)
    assert result["status"] == "conflict"
    assert result["subtask"]["owner"] == "agent-1"
    # the aborted merge leaves the checkout clean
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""


@pytest.mark.skipif(shutil.which("muvue-fake-agent") is None, reason="fake agent not installed")
def test_run_parallel_isolates_nodes_and_merges_in_dependency_order(conn, config, repo):
    first = _task(conn, config, "first", ["a.py"])
    second = _task(conn, config, "second", ["b.py"], depends_on=[first["id"]])
    third = _task(conn, config, "third", ["c.py"])
    _approve_all(conn, config)
    result = runner_mod.run(repo / ".muvue" / "muvue.db", config, repo, parallel=2)
    cwds = {nodes.get_node(conn, n["id"])["worktree"] for n in (first, second, third)}
    assert len(cwds) == 3 and None not in cwds
    merged = [m["node_id"] for m in result["merged"] if m["status"] == "merged"]
    assert sorted(merged) == sorted([first["id"], second["id"], third["id"]])
    assert merged.index(first["id"]) < merged.index(second["id"])
