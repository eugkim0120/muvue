"""v4 section 8a control 5: "Nothing token-shaped is written to disk."
Runs a full serve -> exchange -> approve flow against a real daemon
subprocess and asserts no new file appears under `.muvue/` beyond the
ones `muvue init`/normal operation already produce (config.toml,
muvue.db(+ -wal/-shm), queue.jsonl, history/) -- in particular, no
`.muvue/session` file (v3's removed mechanism) and nothing else new.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from muvue.core import db as core_db, gates, projects
from muvue.core.repo_init import init_repo

TOKEN_RE = re.compile(r"#t=(\S+)")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _snapshot(muvue_dir: Path) -> set[str]:
    return {str(p.relative_to(muvue_dir)) for p in muvue_dir.rglob("*") if p.is_file()}


def test_full_serve_exchange_approve_flow_writes_no_token_shaped_file(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)
    muvue_dir = repo_root / ".muvue"

    conn = core_db.connect(muvue_dir / "muvue.db")
    project = projects.create_project(conn, goal="disk-touch test")
    spec = gates.submit_spec(conn, project_id=project["id"], title="spec", body_md="# x")
    conn.close()

    before = _snapshot(muvue_dir)
    assert not any("session" in f for f in before), f"unexpected pre-existing session-like file: {before}"

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    token = None
    try:
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            m = TOKEN_RE.search(line)
            if m:
                token = m.group(1)
            if "listening on" in line:
                ready = True
                break
        assert ready
        assert token

        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            # The full dashboard flow: exchange the fragment token for
            # the HttpOnly cookie, then use the cookie (no Authorization
            # header at all from here) to perform a human-verb mutation.
            r = client.post("/auth/exchange", json={"token": token})
            assert r.status_code == 200
            assert "muvue_session" in client.cookies

            r = client.post(f"/nodes/{spec['id']}/approve", json={"target": "spec"})
            assert r.status_code == 200
            assert r.json()["status"] == "ready"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    after = _snapshot(muvue_dir)
    new_files = after - before
    assert new_files == set(), f"serve wrote unexpected new file(s) under .muvue/: {new_files}"
    assert not any("session" in f.lower() for f in after), (
        f"a session/token-shaped file exists under .muvue/ after a full serve+exchange+approve "
        f"flow (v4 section 8a control 5 forbids this): {after}"
    )
