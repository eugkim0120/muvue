"""P2a acceptance (plan v4 section 8a / section 10): "Daemon security
tests": bad `Host`, bad `Origin`, form-encoded POST, token in query
string, missing token -- each must 403 *before any side effect*.

These run against a real `muvue serve` subprocess (the pattern already
established in tests/test_serve_integration.py), not a mocked ASGI
transport: the controls under test (Host/Origin header validation,
Content-Type enforcement, token handling) live in real HTTP middleware
and are only meaningfully exercised by a real HTTP client talking to a
real bound socket.
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

from muvue.core import db as core_db, nodes, projects
from muvue.core.repo_init import init_repo

TOKEN_RE = re.compile(r"#t=(\S+)")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunningDaemon:
    def __init__(self, repo_root: Path, port: int, proc: subprocess.Popen, token: str):
        self.repo_root = repo_root
        self.port = port
        self.proc = proc
        self.token = token
        self.base_url = f"http://127.0.0.1:{port}"

    def node_row(self, node_id: int) -> dict:
        conn = core_db.connect(self.repo_root / ".muvue" / "muvue.db")
        try:
            return dict(nodes.get_node(conn, node_id))
        finally:
            conn.close()


@pytest.fixture
def daemon(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)

    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="daemon security test")
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=3,
    )
    task_id = task["id"]
    conn.close()

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    token = None
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
            m = TOKEN_RE.search(line)
            if m:
                token = m.group(1)
            if "listening on" in line:
                ready = True
                break
        assert ready, f"daemon never printed its readiness line; output so far: {lines}"
        assert token, f"daemon never printed a session token; output so far: {lines}"

        d = RunningDaemon(repo_root, port, proc, token)
        d.task_id = task_id  # type: ignore[attr-defined]
        yield d
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _assert_task_untouched(daemon: RunningDaemon):
    row = daemon.node_row(daemon.task_id)
    assert row["status"] == "ready", "a rejected request must never have a side effect"
    assert row["owner"] is None
    assert row["version"] == 1


# -- 1. bad Host header (control 2: DNS rebinding) --------------------------


def test_bad_host_header_403s_before_any_side_effect(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start",
            json={"owner": "attacker"},
            headers={"Host": "evil.example.com", "Authorization": f"Bearer {daemon.token}"},
        )
    assert r.status_code == 403, r.text
    _assert_task_untouched(daemon)


# -- 2. bad Origin header (control 3: CSRF, checked before auth) ------------


def test_bad_origin_header_403s_before_any_side_effect(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start",
            json={"owner": "attacker"},
            headers={
                "Origin": "http://evil.example.com",
                "Authorization": f"Bearer {daemon.token}",
            },
        )
    assert r.status_code == 403, r.text
    _assert_task_untouched(daemon)
    # Also assert the classic CSRF shape: a *valid* token but a foreign
    # Origin is still rejected -- control 3's whole point ("even
    # correctly-authed requests from a foreign origin must be rejected").


# -- 3. form-encoded POST (control 4: HTML-form CSRF) ------------------------


def test_form_encoded_post_403s_before_any_side_effect(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start",
            data={"owner": "attacker"},  # application/x-www-form-urlencoded
            headers={"Authorization": f"Bearer {daemon.token}"},
        )
    assert r.status_code == 403, r.text
    assert r.headers["content-type"].split(";")[0] != "text/html"
    _assert_task_untouched(daemon)


# -- 4. token in query string (control 4: never a query string) ------------


def test_token_in_query_string_is_not_accepted(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start?token={daemon.token}",
            json={"owner": "attacker"},
        )
    assert r.status_code == 403, r.text
    _assert_task_untouched(daemon)


def test_token_in_query_string_alongside_agent_param_is_not_accepted(daemon: RunningDaemon):
    """The `start` endpoint's own `agent=` query param is legitimate
    (non-secret) data; confirm a query-string *token* riding alongside
    it still isn't honored as auth -- query strings leak into logs and
    the `Referer` header, which is exactly why control 4 requires the
    `Authorization` header instead."""
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start?agent=claude&session_token={daemon.token}",
            json={"owner": "attacker"},
        )
    assert r.status_code == 403, r.text
    _assert_task_untouched(daemon)


# -- 5. missing token entirely -----------------------------------------------


def test_missing_token_403s_before_any_side_effect(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(f"/nodes/{daemon.task_id}/start", json={"owner": "attacker"})
    assert r.status_code == 403, r.text
    _assert_task_untouched(daemon)


# -- sanity: the legitimate path actually works on this same daemon --------


def test_valid_request_with_header_token_succeeds(daemon: RunningDaemon):
    with httpx.Client(base_url=daemon.base_url) as client:
        r = client.post(
            f"/nodes/{daemon.task_id}/start",
            json={"owner": "agent-1"},
            headers={"Authorization": f"Bearer {daemon.token}"},
        )
    assert r.status_code == 200, r.text
    row = daemon.node_row(daemon.task_id)
    assert row["status"] == "in_progress"


# -- bind gate (control 1) ---------------------------------------------------


def test_serve_refuses_non_loopback_bind_without_the_escape_hatch(tmp_path: Path):
    repo_root = tmp_path
    init_repo(repo_root)
    port = _free_port()
    proc = subprocess.run(
        [sys.executable, "-m", "muvue", "serve", str(repo_root), "--host", "0.0.0.0",
         "--port", str(port)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0
    assert "i-know-this-is-exposed" in (proc.stdout + proc.stderr)


def test_config_daemon_bind_is_subject_to_the_same_gate(tmp_path: Path):
    """`[daemon] bind` in config.toml is the default host -- and a
    non-loopback value still needs the explicit escape hatch."""
    init_repo(tmp_path)
    cfg = tmp_path / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace('bind = "127.0.0.1"', 'bind = "0.0.0.0"'))
    proc = subprocess.run(
        [sys.executable, "-m", "muvue", "serve", str(tmp_path), "--port", str(_free_port())],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0
    assert "'0.0.0.0'" in proc.stderr


def test_allowed_origins_config_admits_an_extra_origin(tmp_path: Path):
    from fastapi.testclient import TestClient

    from muvue.api import create_app
    from muvue.core.config import MuvueConfig

    init_repo(tmp_path)
    cfg = MuvueConfig()
    cfg.daemon.allowed_origins = ["http://tools.local:9000"]
    client = TestClient(create_app(tmp_path, config=cfg), base_url="http://127.0.0.1")
    assert client.get("/healthz", headers={"Origin": "http://tools.local:9000"}).status_code == 200
    assert client.get("/healthz", headers={"Origin": "http://tools.local:9001"}).status_code == 403
