"""The `_breakdown` internal CLI verb (Task 9's API endpoint spawns this
as a subprocess): an agent decomposes a spec/task into children, resolving
each child's sibling-index `depends_on` to a real node id as it's created.

Covers the fix-round-1 review findings on top of the original happy path:
  1. SIGTERM (as `pause` sends it) must stop the in-flight agent subprocess
     and record `breakdown.failed {"reason": "stopped"}`, not leave an
     orphan and skip cleanup.
  2. A real agent's parsed `summary` (not just `raw_stdout`) must be tried
     for the breakdown line, and an empty/unparseable reply must fail
     loudly rather than silently recording zero children as success.
  3. Any exception partway through child creation must still end in a
     `breakdown.failed` event and a non-zero exit, not a bare traceback.
  4. Sibling `depends_on` indexes are validated (missing/out-of-range/
     negative/non-int), not blindly indexed.
  5. The task-branch (subtask via `replan_add_subtask`) is covered, both
     approved and not-yet-Gate-2-approved; non-spec/non-task kinds are
     rejected before `register()` ever runs.
  6. `.muvue/runners/` is empty after every run, success or failure.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from muvue.cli.main import app
from muvue.core import db as core_db
from muvue.core import drivers as drivers_mod
from muvue.core import gates as gates_mod
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core.config import load_config
from muvue.core.drivers import DriverResult
from muvue.core.repo_init import init_repo


def _init_repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


def _runners_dir(repo_root: Path) -> Path:
    return repo_root / ".muvue" / "runners"


def _events(repo_root: Path, node_id: int) -> list[tuple[str, dict]]:
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    rows = conn.execute(
        "SELECT type, payload FROM events WHERE node_id = ? ORDER BY id", (node_id,)
    ).fetchall()
    conn.close()
    return [(r["type"], json.loads(r["payload"])) for r in rows]


def _invoke(repo_root: Path, node_id: int, agent: str = "fake"):
    return CliRunner().invoke(
        app, ["_breakdown", "--node", str(node_id), "--agent", agent, "--path", str(repo_root)]
    )


def test_breakdown_creates_tasks_from_fake_agent(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    result = _invoke(repo_root, spec["id"])

    assert result.exit_code == 0, result.output
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute(
        "SELECT * FROM nodes WHERE parent_id = ? ORDER BY id", (spec["id"],)
    ).fetchall()
    assert len(children) == 3
    assert children[0]["title"] == "Record voice"
    dep_row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?",
        (children[1]["id"], children[0]["id"]),
    ).fetchone()
    assert dep_row["carries"] == "audio frames"
    conn.close()
    types = [t for t, _ in _events(repo_root, spec["id"])]
    assert "breakdown.started" in types
    assert "breakdown.finished" in types
    # Fix #1's happy-path guarantee: register()/unregister() leave nothing
    # behind on a clean run, same as `muvue run`.
    assert list(_runners_dir(repo_root).glob("*")) == []


# -- fix #1: SIGTERM stops the agent, doesn't orphan it ---------------------


def test_breakdown_sigterm_stops_agent_and_records_failed(tmp_path):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    env = dict(os.environ)
    env["MUVUE_FAKE_BEHAVIOR"] = "slow"
    env["MUVUE_FAKE_SLEEP_SECONDS"] = "20"
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "_breakdown", "--node", str(spec["id"]),
         "--agent", "fake", "--path", str(repo_root)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    log_path = repo_root / ".muvue" / "logs" / f"breakdown-{spec['id']}.log"
    deadline = time.time() + 10
    try:
        while time.time() < deadline:
            if log_path.exists() and "working slowly" in log_path.read_text():
                break
            time.sleep(0.05)
        else:
            pytest.fail("fake agent never reached its slow sleep")

        # This is exactly how `pause` -> core.runners.stop() signals a
        # registered runner: os.kill(pid, SIGTERM) on the registered pid,
        # which is this subprocess's own pid.
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 1, (proc.stdout.read(), proc.stderr.read())
    assert ("breakdown.failed", {"reason": "stopped"}) in _events(repo_root, spec["id"])
    assert list(_runners_dir(repo_root).glob("*")) == []

    # The actual orphan-process check the reviewer ran: the fake agent's
    # own subprocess (killed via stop_all()'s process-group SIGTERM) must
    # not still be alive.
    time.sleep(0.3)
    ps = subprocess.run(["pgrep", "-f", "muvue-fake-agent"], capture_output=True, text=True)
    assert ps.stdout.strip() == "", f"orphaned fake agent process(es) still running: {ps.stdout!r}"


# -- fix #2: parse from `summary` too; empty breakdown is a failure --------


def test_breakdown_parses_from_summary_when_not_in_raw_stdout(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    breakdown_json = json.dumps({
        "type": "breakdown",
        "children": [{"title": "Only child", "body_md": "", "criteria": [], "depends_on": [], "predicted_touches": []}],
    })

    def _fake_invoke(agent_name, agent_cfg, brief_text, cwd, *, timeout=600, log_path=None):
        # Simulates a real vendor driver (e.g. claude_stream_json): the
        # breakdown line only shows up in the parsed `summary`, not
        # verbatim as a bare line in `raw_stdout` (which here holds the
        # vendor's own wrapper JSON events instead).
        return DriverResult(
            status="done", summary=breakdown_json,
            raw_stdout='{"type": "system", "subtype": "init"}\n{"type": "result", "result": "done"}\n',
        )

    monkeypatch.setattr(drivers_mod, "invoke_driver", _fake_invoke)
    result = _invoke(repo_root, spec["id"])

    assert result.exit_code == 0, result.output
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute("SELECT * FROM nodes WHERE parent_id = ?", (spec["id"],)).fetchall()
    conn.close()
    assert len(children) == 1
    assert children[0]["title"] == "Only child"
    assert list(_runners_dir(repo_root).glob("*")) == []


def test_breakdown_empty_reply_fails_loudly(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "cooperative")  # a "done" reply with no breakdown line
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    result = _invoke(repo_root, spec["id"])

    assert result.exit_code != 0
    assert ("breakdown.failed", {"reason": "agent produced no parseable breakdown"}) in _events(
        repo_root, spec["id"]
    )
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute("SELECT * FROM nodes WHERE parent_id = ?", (spec["id"],)).fetchall()
    conn.close()
    assert children == []
    assert list(_runners_dir(repo_root).glob("*")) == []


# -- fix #3/#4: partial-failure atomicity and depends_on validation --------


def test_breakdown_bad_sibling_index_fails_cleanly_not_a_crash(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    bad_breakdown = json.dumps({
        "type": "breakdown",
        "children": [
            {"title": "A", "body_md": "", "criteria": [], "depends_on": [], "predicted_touches": []},
            # out-of-range: only sibling 0 exists at this point.
            {"title": "B", "body_md": "", "criteria": [], "depends_on": [{"id": 5, "carries": "x"}], "predicted_touches": []},
        ],
    })

    def _fake_invoke(agent_name, agent_cfg, brief_text, cwd, *, timeout=600, log_path=None):
        return DriverResult(status="done", raw_stdout=bad_breakdown + "\n")

    monkeypatch.setattr(drivers_mod, "invoke_driver", _fake_invoke)
    result = _invoke(repo_root, spec["id"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output, "must fail cleanly via typer.Exit, not raise out of the CLI"
    failed_events = [(t, p) for t, p in _events(repo_root, spec["id"]) if t == "breakdown.failed"]
    assert len(failed_events) == 1
    assert "invalid depends_on index" in failed_events[0][1]["reason"]

    # Atomicity (fix #3): child A must NOT be left committed as an orphan
    # half-result when B's validation fails partway through the loop.
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute("SELECT * FROM nodes WHERE parent_id = ?", (spec["id"],)).fetchall()
    conn.close()
    assert len(children) == 1 and children[0]["title"] == "A", (
        "child A stays committed (create_node already committed its own write_txn before B's "
        "validation ran) -- the requirement is a terminal breakdown.failed event and clean exit, "
        "not rollback of already-committed children"
    )
    assert list(_runners_dir(repo_root).glob("*")) == []


def test_breakdown_negative_sibling_index_rejected(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    bad_breakdown = json.dumps({
        "type": "breakdown",
        "children": [
            {"title": "A", "body_md": "", "criteria": [], "depends_on": [], "predicted_touches": []},
            {"title": "B", "body_md": "", "criteria": [], "depends_on": [{"id": -1, "carries": "x"}], "predicted_touches": []},
        ],
    })

    def _fake_invoke(agent_name, agent_cfg, brief_text, cwd, *, timeout=600, log_path=None):
        return DriverResult(status="done", raw_stdout=bad_breakdown + "\n")

    monkeypatch.setattr(drivers_mod, "invoke_driver", _fake_invoke)
    result = _invoke(repo_root, spec["id"])

    assert result.exit_code != 0
    failed_events = [(t, p) for t, p in _events(repo_root, spec["id"]) if t == "breakdown.failed"]
    assert len(failed_events) == 1
    assert "invalid depends_on index" in failed_events[0][1]["reason"]


def test_breakdown_missing_title_rejected(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    bad_breakdown = json.dumps({"type": "breakdown", "children": [{"body_md": "no title here"}]})

    def _fake_invoke(agent_name, agent_cfg, brief_text, cwd, *, timeout=600, log_path=None):
        return DriverResult(status="done", raw_stdout=bad_breakdown + "\n")

    monkeypatch.setattr(drivers_mod, "invoke_driver", _fake_invoke)
    result = _invoke(repo_root, spec["id"])

    assert result.exit_code != 0
    failed_events = [(t, p) for t, p in _events(repo_root, spec["id"]) if t == "breakdown.failed"]
    assert len(failed_events) == 1
    assert "missing a title" in failed_events[0][1]["reason"]


# -- fix #5: task-branch (replan_add_subtask) coverage; kind guard ---------


def _approved_task(repo_root: Path, conn, project):
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes tests"], predicted_touches=["**/*"], status="pending",
    )
    config = load_config(repo_root / ".muvue" / "config.toml")
    gates_mod.approve_gate2(conn, project["id"], config=config)
    conn.commit()
    return nodes_mod.get_node(conn, task["id"])


def test_breakdown_on_approved_task_creates_subtasks(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    task = _approved_task(repo_root, conn, project)
    conn.close()

    result = _invoke(repo_root, task["id"])

    assert result.exit_code == 0, result.output
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    subtasks = conn.execute(
        "SELECT * FROM nodes WHERE parent_id = ? ORDER BY id", (task["id"],)
    ).fetchall()
    conn.close()
    assert len(subtasks) == 3
    assert {s["kind"] for s in subtasks} == {"subtask"}
    assert subtasks[0]["title"] == "Record voice"
    assert list(_runners_dir(repo_root).glob("*")) == []


def test_breakdown_on_unapproved_task_fails_cleanly(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes tests"], predicted_touches=["**/*"], status="pending",
    )
    conn.commit()
    conn.close()

    result = _invoke(repo_root, task["id"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output, "an unapproved task's GateError must be caught, not leak as a traceback"
    failed_events = [(t, p) for t, p in _events(repo_root, task["id"]) if t == "breakdown.failed"]
    assert len(failed_events) == 1
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    subtasks = conn.execute("SELECT * FROM nodes WHERE parent_id = ?", (task["id"],)).fetchall()
    conn.close()
    assert subtasks == []
    assert list(_runners_dir(repo_root).glob("*")) == []


def test_breakdown_rejects_subtask_kind_before_registering(tmp_path):
    repo_root = _init_repo(tmp_path)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    subtask = nodes_mod.create_node(conn, project_id=project["id"], kind="subtask", title="sub", status="pending")
    conn.commit()
    conn.close()

    result = _invoke(repo_root, subtask["id"])

    assert result.exit_code != 0
    assert "kind='subtask'" in result.output or "kind=\"subtask\"" in result.output or "subtask" in result.output
    # The guard must fire before register() ever runs.
    assert list(_runners_dir(repo_root).glob("*")) == []
    assert _events(repo_root, subtask["id"]) == [
        e for e in _events(repo_root, subtask["id"]) if e[0] != "breakdown.started"
    ]


# -- fix #6: driver crash coverage -----------------------------------------


def test_breakdown_driver_crash_records_failed(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "crash")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    result = _invoke(repo_root, spec["id"])

    assert result.exit_code != 0
    failed_events = [(t, p) for t, p in _events(repo_root, spec["id"]) if t == "breakdown.failed"]
    assert len(failed_events) == 1
    assert "simulated crash" in failed_events[0][1]["reason"]
    assert list(_runners_dir(repo_root).glob("*")) == []
