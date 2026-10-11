"""Mission control phase 1: a runner invocation is a `launched` session
from before the driver starts until it returns; the driver gets
MUVUE_SESSION, and the agent's own hook lines fold into that row."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from muvue import _hook
from muvue.core import db as core_db, drivers, gates, nodes, projects, runner as runner_mod, sessions
from muvue.core.config import AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig, SessionsConfig
from muvue.core.drivers import DriverResult
from muvue.core.repo_init import init_repo

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def test_invoke_driver_passes_env_and_reports_the_pid(tmp_path):
    cfg = AgentConfig(command='printf %s "$MUVUE_SESSION"', cost_model="tokens")
    pids: list[int] = []
    result = drivers.invoke_driver(
        "probe", cfg, "", tmp_path, env={**os.environ, "MUVUE_SESSION": "run-abc"}, on_spawn=pids.append,
    )
    assert result.status == "done"
    assert result.raw_stdout == "run-abc"
    assert len(pids) == 1 and pids[0] > 0


def test_a_failing_on_spawn_kills_the_driver(tmp_path):
    cfg = AgentConfig(command="sleep 30", cost_model="tokens")
    seen: list[int] = []

    def boom(pid: int) -> None:
        seen.append(pid)
        raise RuntimeError("could not record the pid")

    with pytest.raises(RuntimeError, match="could not record the pid"):
        drivers.invoke_driver("probe", cfg, "", tmp_path, on_spawn=boom)
    with pytest.raises(ProcessLookupError):
        os.killpg(seen[0], 0)
    assert not drivers._ACTIVE


def test_run_node_records_a_launched_session_and_folds_the_agents_own_id(tmp_path, monkeypatch):
    monkeypatch.delenv("MUVUE_SESSION", raising=False)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True, env=GIT_ENV)
    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    config = MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"stub": AgentConfig(command="unused", cost_model="tokens")},
        routing=RoutingConfig(spec="stub", task="stub", subtask="stub"),
    )
    project = projects.create_project(conn, goal="g")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", criteria=["c"],
        criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    seen: dict = {}

    def agent(agent_name, agent_cfg, brief, cwd, *, log_path=None, env=None, on_spawn=None):
        run_id = env[sessions.LAUNCHED_SESSION_ENV]
        on_spawn(4242)
        seen["run_id"] = run_id
        seen["during"] = dict(conn.execute("SELECT origin, pid, ended_at, node_id FROM sessions WHERE id = ?", (run_id,)).fetchone())
        # What the agent's own Claude Code hooks would spool.
        monkeypatch.setenv("MUVUE_SESSION", run_id)
        _hook.run("pre-tool-use", str(tmp_path), {
            "session_id": "claude-own-1", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"},
        })
        return DriverResult(status="failed", error="stub agent gives up")

    runner_mod.run_node(conn, nodes.get_node(conn, task["id"]), "stub", config.agents["stub"], config, tmp_path, invoke=agent)

    run_id = seen["run_id"]
    assert run_id.startswith("run-")
    assert seen["during"] == {"origin": "launched", "pid": 4242, "ended_at": None, "node_id": task["id"]}
    after = conn.execute("SELECT * FROM sessions WHERE id = ?", (run_id,)).fetchone()
    assert after["ended_at"] is not None
    assert after["last_action"] == "run ended"
    assert conn.execute("SELECT alias_of FROM sessions WHERE id = 'claude-own-1'").fetchone()["alias_of"] == run_id
    listed = sessions.list_sessions(conn, SessionsConfig(), include_ended=True)
    assert [s["id"] for s in listed] == [run_id]
    conn.close()


def test_end_launched_does_not_overwrite_an_earlier_end(tmp_path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    run_id = sessions.start_launched(conn, node_id=node["id"], project_id=project["id"], agent="claude", cwd=str(tmp_path))
    sessions.end_launched(conn, run_id)
    first = conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"]
    sessions.end_launched(conn, run_id)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] == first
    conn.close()
