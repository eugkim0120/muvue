"""Sign-in plan Task 2: a same-user Unix socket lets `muvue link` get a
fresh one-time dashboard link from a running daemon, reviving an expired
session with a new token instead of requiring a restart."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from muvue.core import control
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo

URL_RE = re.compile(r"^dashboard \(one-time link, works once\): (\S+)$", re.MULTILINE)
TOKEN_RE = re.compile(r"^api token: (\S+)$", re.MULTILINE)


@pytest.fixture
def server(tmp_path: Path):
    session = SessionManager()
    path = tmp_path / "d" / "x.sock"
    srv = control.ControlServer(path, session, base_url="http://127.0.0.1:9")
    srv.start()
    yield srv, session, path
    srv.stop()


def test_link_on_a_live_session_returns_a_working_nonce_and_the_same_token(server):
    _srv, session, path = server
    token = session.token
    reply = control.request_link(path)
    assert reply["renewed"] is False
    assert reply["token"] == token
    assert reply["url"].startswith("http://127.0.0.1:9/#n=")
    assert session.consume_nonce(reply["url"].split("#n=", 1)[1]) == "ok"


def test_link_on_an_expired_session_renews_it(server):
    _srv, session, path = server
    old = session.token
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=9)
    reply = control.request_link(path)
    assert reply["renewed"] is True
    assert reply["token"] == session.token != old
    assert session.check(old) == "invalid"
    assert session.consume_nonce(reply["url"].split("#n=", 1)[1]) == "ok"


def test_socket_is_owner_only(server):
    _srv, _session, path = server
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_unknown_request_gets_an_error_reply(server):
    _srv, _session, path = server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(path))
        s.sendall(b"token please\n")
        line = s.makefile("r").readline()
    assert "unknown request" in line


def test_stop_removes_the_socket(tmp_path: Path):
    path = tmp_path / "x.sock"
    srv = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    srv.start()
    srv.stop()
    assert not path.exists()
    with pytest.raises(control.LinkError):
        control.request_link(path)


def test_start_replaces_a_stale_socket_file(tmp_path: Path):
    path = tmp_path / "x.sock"
    path.write_text("left over from a crashed daemon")
    srv = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    srv.start()
    try:
        assert control.request_link(path)["url"].startswith("http://127.0.0.1:9/#n=")
    finally:
        srv.stop()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_muvue_link_with_no_daemon_fails_loudly(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    r = subprocess.run([sys.executable, "-m", "muvue", "link", str(repo)], capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "no `muvue serve` is running for" in r.stderr


def test_serve_then_link_gives_a_link_that_signs_in(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        lines: list[str] = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            lines.append(line)
            if "listening on" in line:
                break
        out = "".join(lines)
        assert "listening on" in out, out
        assert f"run `muvue link {repo}`" in out

        r = subprocess.run([sys.executable, "-m", "muvue", "link", str(repo)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        url = URL_RE.search(r.stdout).group(1)
        assert TOKEN_RE.search(r.stdout).group(1) == TOKEN_RE.search(out).group(1)
        nonce = url.split("#n=", 1)[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as c:
            ex = c.post("/auth/exchange", json={"nonce": nonce})
            assert ex.status_code == 200, ex.text
            assert c.get("/auth/check").status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)
