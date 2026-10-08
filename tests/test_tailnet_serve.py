"""End to end through `muvue serve`: a local process must not become a
tailnet peer by sending forwarding headers. Needs a real tailscaled, whose
own address resolves to its owner."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from muvue.core import tailnet
from muvue.core.repo_init import init_repo


def _own_tailnet_login() -> tuple[str, str]:
    try:
        tailnet.localapi_reachable()
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True).stdout.split()[0]
        return out, tailnet.TailnetResolver().peer(out).login
    except (tailnet.TailnetError, OSError, subprocess.CalledProcessError, AttributeError, IndexError):
        pytest.skip("no usable tailscaled on this host")


def test_forwarded_headers_never_set_identity(tmp_path: Path):
    own_ip, login = _own_tailnet_login()
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    cfg = repo / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace("tailnet_logins = []", f'tailnet_logins = ["{login}"]'))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        url = f"http://127.0.0.1:{port}/auth/whoami"
        deadline = time.monotonic() + 20
        while True:
            try:
                req = urllib.request.Request(url, headers={"X-Forwarded-For": own_ip, "X-Real-IP": own_ip})
                body = json.load(urllib.request.urlopen(req, timeout=2))
                break
            except (urllib.error.URLError, ConnectionError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        assert body == {"login": None}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
