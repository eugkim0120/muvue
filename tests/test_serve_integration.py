"""P2 acceptance #2: kill the daemon mid-task -> node reverts to `ready`
(with attempts incremented) when `muvue serve` is restarted.

Spawns a real `muvue serve` subprocess (per the P2 process instructions:
"where you do need a real subprocess... write an integration test that
spawns `muvue serve`... keep it deterministic -- no arbitrary sleeps
beyond what's needed"). We simulate "killed mid-task" by giving the node
an already-expired lease before the daemon (re)starts, which is exactly
what a real crash-and-restart would leave behind (the daemon holds no
in-memory state; only the DB's `lease_until` column records the lease),
then poll for the daemon's readiness line instead of sleeping blindly.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muvue.core import db as core_db, nodes, projects
from muvue.core.repo_init import init_repo


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_kill_daemon_mid_task_reverts_node_to_ready_on_restart(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)

    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="serve integration test")
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=3,
    )
    # Simulate: the daemon started this node, then the process was killed
    # before the lease could be renewed or the node completed. All that
    # survives a crash is the DB row -- an already-expired lease.
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    assert nodes.get_node(conn, task["id"])["status"] == "in_progress"
    conn.close()

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        ready = False
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            lines.append(line)
            if "listening on" in line:
                ready = True
                break
        assert ready, f"daemon never printed its readiness line; output so far: {lines}"

        # Reconcile-on-start already ran (it runs before the listening
        # line is printed), so the DB should already show the reverted
        # node -- read it directly rather than sleeping.
        check_conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
        try:
            row = nodes.get_node(check_conn, task["id"])
        finally:
            check_conn.close()
        assert row["status"] == "ready"
        assert row["owner"] is None
        assert row["lease_until"] is None
        # v4 section 3/5: a daemon-restart lease reclaim increments
        # lease_expiries, never attempts (v3 conflated the two, so a
        # daemon restart could burn a node's retries).
        assert row["attempts"] == 0
        assert row["lease_expiries"] == 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
