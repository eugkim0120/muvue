"""Plan section 5 "Emergency stop": `pause` kills runner processes,
refuses `start`, flips the dashboard red; `resume` reverses. Plus the CLI
human verbs that used to be stubs (`reject`, `ack`), and actor evidence
for human verbs issued from inside an agent's process tree (section 4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import events, nodes, projects, runners
from muvue.core.repo_init import init_repo

SLOW_CMD = "MUVUE_FAKE_SLEEP_SECONDS=97 muvue-fake-agent --behavior slow"


def _cli(repo: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "muvue", *args, "--path", str(repo)],
                          cwd=repo, capture_output=True, text=True, timeout=60, **kw)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    cfg = tmp_path / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace('command = "muvue-fake-agent"', f'command = "{SLOW_CMD}"'))
    return tmp_path


def _conn(repo: Path):
    return core_db.connect(repo / ".muvue" / "muvue.db")


def _ready_task(repo: Path) -> tuple[int, int]:
    c = _conn(repo)
    try:
        p = projects.set_phase(c, projects.create_project(c, goal="pause")["id"], "executing")
        t = nodes.create_node(c, project_id=p["id"], kind="task", title="t", status="ready",
                              criteria=["ok"], criteria_mode="auto")
        return p["id"], t["id"]
    finally:
        c.close()


def _wait_for(pred, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _slow_agents_running() -> bool:
    out = subprocess.run(["pgrep", "-f", "MUVUE_FAKE_SLEEP_SECONDS=97"], capture_output=True, text=True)
    return bool(out.stdout.strip())


def test_pause_kills_the_runner_and_releases_its_node(repo):
    project_id, task_id = _ready_task(repo)
    run = subprocess.Popen([sys.executable, "-m", "muvue", "run", "--path", str(repo)],
                           cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        def started():
            c = _conn(repo)
            try:
                return nodes.get_node(c, task_id)["status"] == "in_progress"
            finally:
                c.close()
        assert _wait_for(started), "runner never started the node"
        assert _wait_for(lambda: runners.live(repo)), "runner never registered"
        assert _slow_agents_running()

        out = _cli(repo, "pause", str(project_id))
        assert out.returncode == 0, out.stderr
        body = json.loads(out.stdout)
        assert body["project"]["phase"] == "paused"
        assert body["stopped_runners"] == [run.pid]

        run.wait(timeout=20)
        result = json.loads(run.stdout.read())
        assert result["paused"]["reason"] == "stopped"
    finally:
        if run.poll() is None:
            run.kill()
    assert _wait_for(lambda: not _slow_agents_running(), timeout=5), "driver survived pause"
    assert runners.live(repo) == []
    c = _conn(repo)
    try:
        node = nodes.get_node(c, task_id)
        types = [r["type"] for r in c.execute("SELECT type FROM events WHERE node_id = ?", (task_id,))]
    finally:
        c.close()
    assert node["status"] == "ready"
    assert node["attempts"] == 0
    assert node["owner"] is None
    assert "node.released" in types
    log = (repo / ".muvue" / "logs" / f"{task_id}.log").read_text()
    assert "working slowly" in log


def test_start_is_refused_while_paused_and_resume_reverses(repo):
    project_id, task_id = _ready_task(repo)
    assert _cli(repo, "pause", str(project_id)).returncode == 0
    refused = _cli(repo, "start", str(task_id), "--owner", "a")
    assert refused.returncode != 0
    out = _cli(repo, "resume", str(project_id))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["project"]["phase"] == "executing"
    assert _cli(repo, "start", str(task_id), "--owner", "a").returncode == 0


def test_resume_of_a_project_that_is_not_paused_fails(repo):
    project_id, _ = _ready_task(repo)
    out = _cli(repo, "resume", str(project_id))
    assert out.returncode == 1
    assert "not paused" in out.stderr


def test_cli_reject_sends_a_review_back_with_feedback(repo):
    from muvue.core.config import MuvueConfig

    _, task_id = _ready_task(repo)
    c = _conn(repo)
    try:
        c.execute("UPDATE nodes SET risk_tier = 'high' WHERE id = ?", (task_id,))
        nodes.start(c, task_id, owner="a")
        nodes.done(c, task_id, owner="a", config=MuvueConfig())
        assert nodes.get_node(c, task_id)["status"] == "review"
    finally:
        c.close()
    out = _cli(repo, "reject", f"review:{task_id}", "--feedback", "tests missing")
    assert out.returncode == 0, out.stderr
    c = _conn(repo)
    try:
        assert nodes.get_node(c, task_id)["status"] == "in_progress"
        feedback = c.execute("SELECT text FROM notes WHERE kind = 'feedback'").fetchone()["text"]
    finally:
        c.close()
    assert feedback == "tests missing"


def test_cli_ack_closes_an_inbox_item_and_records_the_ack(repo):
    c = _conn(repo)
    try:
        eid = events.record_event(c, project_id=None, node_id=None, actor="hook",
                                  type_="inbox.unattributed_commit", payload={})
    finally:
        c.close()
    out = _cli(repo, "ack", str(eid))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["acked_at"] is not None
    c = _conn(repo)
    try:
        acks = c.execute("SELECT payload FROM events WHERE type = 'event.acked'").fetchall()
    finally:
        c.close()
    assert [json.loads(a["payload"])["event_id"] for a in acks] == [eid]


def test_human_verb_from_inside_an_agent_process_tree_is_recorded_as_the_agent(repo, tmp_path_factory):
    """Plan section 4: detection, not prevention. Run `muvue ack` from a
    parent process named `codex`; the ack goes through and is recorded as
    `actor=agent`, `actor_evidence=agent_parent:codex`."""
    bindir = tmp_path_factory.mktemp("fakebin")
    (bindir / "codex").symlink_to("/bin/sh")
    c = _conn(repo)
    try:
        eid = events.record_event(c, project_id=None, node_id=None, actor="hook",
                                  type_="inbox.audit_drift_signal", payload={})
    finally:
        c.close()
    cmd = f"{sys.executable} -m muvue ack {eid} --path {repo}"
    out = subprocess.run([str(bindir / "codex"), "-c", cmd], capture_output=True, text=True,
                         timeout=60, cwd=repo, env={**os.environ})
    assert out.returncode == 0, out.stderr
    c = _conn(repo)
    try:
        row = c.execute("SELECT actor, actor_evidence FROM events WHERE type = 'event.acked'").fetchone()
    finally:
        c.close()
    assert (row["actor"], row["actor_evidence"]) == ("agent", "agent_parent:codex")
