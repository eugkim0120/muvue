"""Regressions found by the live P5 run against claude 2.1.281 (W11 of
the v4 delta closure)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from muvue.core import db as core_db, gates, nodes, projects, runner as runner_mod
from muvue.core.config import AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig
from muvue.core.drivers import DriverResult
from muvue.core.repo_init import init_repo

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True, env=GIT_ENV)
    init_repo(tmp_path)  # installs the post-commit shim that spools to the queue
    return tmp_path


def _config() -> MuvueConfig:
    return MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"stub": AgentConfig(command="unused", cost_model="tokens")},
        routing=RoutingConfig(spec="stub", task="stub", subtask="stub"),
    )


def test_the_agents_commits_are_linked_before_done(tmp_path):
    """The live run's agent committed with the trailer, but its commit
    sat in the hook spool: nothing drains it during `muvue run` when no
    daemon is up, so `done` judged the node with no actual touches or
    diff."""
    repo = _repo(tmp_path)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    config = _config()
    project = projects.create_project(conn, goal="g")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", criteria=["c"],
        criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)

    def committing_agent(agent_name, agent_cfg, brief, cwd, *, log_path=None):
        (Path(cwd) / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=cwd, check=True, env=GIT_ENV)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"work\n\nMuvue-Node: {task['id']}"],
            cwd=cwd, check=True, env=GIT_ENV,
        )
        return DriverResult(status="done", summary="did it")

    node = nodes.get_node(conn, task["id"])
    runner_mod.run_node(conn, node, "stub", config.agents["stub"], config, repo, invoke=committing_agent)

    assert conn.execute("SELECT COUNT(*) FROM node_commits WHERE node_id = ?", (task["id"],)).fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT path FROM actual_touches WHERE node_id = ?", (task["id"],))] == ["a.py"]
    conn.close()


def test_agent_prompt_names_the_check_muvue_will_run():
    """The live agent ran `python -m pytest`, which its permissions
    didn't allow and which isn't what muvue runs; it never learned the
    configured command."""
    config = MuvueConfig(checks=ChecksConfig(test=".venv/bin/python -m pytest -q", lint="ruff check ."))
    prompt = runner_mod.agent_prompt(7, "BRIEF", checks=config.checks)
    assert "`.venv/bin/python -m pytest -q`" in prompt
    assert "`ruff check .`" in prompt
    assert prompt.endswith("BRIEF")
