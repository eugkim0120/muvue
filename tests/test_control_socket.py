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


def _raw(path: Path, data: bytes) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(str(path))
        s.sendall(data)
        return s.makefile("r").readline()


def test_bad_bytes_do_not_kill_the_control_thread(server):
    srv, _session, path = server
    assert "unknown request" in _raw(path, b"\xff\xfe\n")
    assert srv._thread.is_alive()
    assert control.request_link(path)["url"].startswith("http://127.0.0.1:9/#n=")


def test_oversized_request_is_rejected_and_server_survives(server):
    srv, _session, path = server
    assert "too long" in _raw(path, b"a" * 5000 + b"\n")
    assert srv._thread.is_alive()
    assert control.request_link(path)["renewed"] is False


def test_a_failing_handler_does_not_end_the_accept_loop(server, monkeypatch, capsys):
    srv, _session, path = server
    calls = {"n": 0}
    real = srv._handle

    def flaky(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        real(conn)

    monkeypatch.setattr(srv, "_handle", flaky)
    with pytest.raises(control.LinkError):
        control.request_link(path)
    assert control.request_link(path)["url"]
    assert "boom" in capsys.readouterr().err


def _silent_listener(path: Path) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.listen(1)
    return s


def test_request_link_times_out_with_a_link_error(tmp_path: Path):
    path = tmp_path / "s.sock"
    listener = _silent_listener(path)
    try:
        with pytest.raises(control.LinkError, match="did not reply"):
            control.request_link(path, timeout=0.3)
    finally:
        listener.close()


def test_request_link_rejects_a_non_dict_reply(tmp_path: Path):
    import threading

    path = tmp_path / "s.sock"
    listener = _silent_listener(path)

    def answer():
        conn, _ = listener.accept()
        with conn:
            conn.sendall(b"[1, 2]\n")

    t = threading.Thread(target=answer)
    t.start()
    try:
        with pytest.raises(control.LinkError, match="unexpected reply"):
            control.request_link(path)
    finally:
        t.join()
        listener.close()


def test_request_link_rejects_non_json(tmp_path: Path):
    import threading

    path = tmp_path / "s.sock"
    listener = _silent_listener(path)

    def answer():
        conn, _ = listener.accept()
        with conn:
            conn.sendall(b"not json\n")

    t = threading.Thread(target=answer)
    t.start()
    try:
        with pytest.raises(control.LinkError, match="not JSON"):
            control.request_link(path)
    finally:
        t.join()
        listener.close()


def test_stop_leaves_a_socket_that_another_daemon_bound(tmp_path: Path):
    path = tmp_path / "x.sock"
    a = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    a.start()
    path.unlink()
    other = _silent_listener(path)
    try:
        a.stop()
        assert path.exists()
    finally:
        other.close()


def test_start_refuses_when_a_live_daemon_answers(tmp_path: Path):
    path = tmp_path / "x.sock"
    a = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    a.start()
    try:
        b = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
        with pytest.raises(RuntimeError, match="already answering"):
            b.start()
        assert control.request_link(path)["url"]  # A still owns it
    finally:
        a.stop()


def test_start_tightens_an_existing_directory_to_0700(tmp_path: Path):
    d = tmp_path / "d"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    srv = control.ControlServer(d / "x.sock", SessionManager(), base_url="http://127.0.0.1:9")
    srv.start()
    try:
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    finally:
        srv.stop()


def test_start_refuses_a_directory_owned_by_someone_else(tmp_path: Path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(os, "getuid", lambda: os.stat(d).st_uid + 1)
    srv = control.ControlServer(d / "x.sock", SessionManager(), base_url="http://127.0.0.1:9")
    with pytest.raises(PermissionError, match="not owned"):
        srv.start()
    assert not (d / "x.sock").exists()


def test_other_users_are_refused(server, monkeypatch):
    _srv, _session, path = server
    monkeypatch.setattr(control, "_peer_is_self", lambda conn: False)
    with pytest.raises(control.LinkError, match="refused"):
        control.request_link(path)


def test_second_serve_on_the_same_repo_leaves_the_running_daemons_port_file(tmp_path: Path, monkeypatch):
    from muvue.core.daemon import port_file_path, write_port_file

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    sock_path = control.socket_path(repo)
    sock_path.parent.mkdir(parents=True)
    write_port_file(repo, port=4321, pid=99999)
    before = port_file_path(repo).read_text()
    listener = _silent_listener(sock_path)
    try:
        env = {**os.environ, "HOME": str(home)}
        r = subprocess.run(
            [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(_free_port())],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert r.returncode == 1
        assert r.stderr.strip().count("\n") == 0
        assert "already answering" in r.stderr
        assert "Traceback" not in r.stderr
        assert "api token" not in r.stdout
        assert port_file_path(repo).read_text() == before
    finally:
        listener.close()


def test_link_with_a_corrupt_port_file_fails_cleanly(tmp_path: Path, monkeypatch):
    from muvue.core.daemon import port_file_path

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    pf = port_file_path(repo)
    pf.parent.mkdir(parents=True)
    pf.write_text("{trunc")
    env = {**os.environ, "HOME": str(home)}
    r = subprocess.run([sys.executable, "-m", "muvue", "link", str(repo)], capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "unreadable" in r.stderr
    assert "Traceback" not in r.stderr
