"""v4 section 10 adversarial behaviours that need real processes: a
TTY obtained through `script` under an agent CLI, and reading the
daemon's port file. Each is blocked, or recorded with the right
`actor_evidence` and surfaced; the assertions say which."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import fake_agent
from muvue.core import db as core_db, gates, projects
from muvue.core.repo_init import init_repo


@pytest.mark.skipif(shutil.which("script") is None, reason="needs util-linux `script`")
def test_script_tty_under_an_agent_is_recorded_as_the_agent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="tty")
    spec = gates.submit_spec(conn, project_id=project["id"], title="s", body_md="b")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    proc = fake_agent.adversarial_script_tty_verb(repo, bin_dir, ["approve", f"spec:{spec['id']}"])
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Detection, not prevention (v4 section 4): the approval happens, and
    # the event says an agent did it.
    event = conn.execute(
        "SELECT actor, actor_evidence FROM events WHERE type = 'node.ready' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert (event["actor"], event["actor_evidence"]) == ("agent", "agent_parent:codex")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_file_gives_an_agent_no_credential(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "HOME": str(home)},
    )
    token = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line.startswith("api token: "):
                token = line.split(": ", 1)[1].strip()
            if "listening on" in line:
                break
        assert token

        found = fake_agent.adversarial_read_port_file(repo)
        assert set(found) == {"port", "pid"}
        assert token not in json.dumps(found)

        # Everything the file offers, tried as a credential.
        with httpx.Client(base_url=f"http://127.0.0.1:{found['port']}") as client:
            for guess in (str(found["pid"]), str(found["port"]), "none"):
                r = client.post(
                    "/projects/1/pause",
                    headers={"Authorization": f"Bearer {guess}", "Content-Type": "application/json"},
                )
                assert r.status_code == 403
    finally:
        proc.terminate()
        proc.wait(timeout=10)
