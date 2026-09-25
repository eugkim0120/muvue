"""v4 section 2 layout: `~/.muvue/daemon/<repo-hash>.json` -- 0600,
daemon port + PID only, no token on disk (section 8a). Driven through a
real `muvue serve` subprocess with HOME pointed at a temp dir."""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muvue.core import daemon
from muvue.core.repo_init import init_repo


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_serve_writes_port_file_without_token_and_removes_it_on_exit(tmp_path: Path, stop_signal):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    env = {**os.environ, "HOME": str(home)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
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

        port_files = list((home / ".muvue" / "daemon").glob("*.json"))
        assert len(port_files) == 1
        port_file = port_files[0]
        assert stat.S_IMODE(port_file.stat().st_mode) == 0o600
        raw = port_file.read_text()
        assert json.loads(raw) == {"port": port, "pid": proc.pid}
        assert token not in raw
    finally:
        proc.send_signal(stop_signal)
        proc.wait(timeout=20)
    assert not port_file.exists()


def test_port_file_path_is_per_repo(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = daemon.port_file_path(tmp_path / "a")
    b = daemon.port_file_path(tmp_path / "b")
    assert a != b
    assert a.parent == tmp_path / ".muvue" / "daemon"
