"""v4 section 8a control 7: "`doctor` verifies 1-4 by issuing live
probe requests against a running daemon and fails loudly." Covers both
of docs/decisions.md #87's branches: no daemon already running (a
throwaway one is spun up against a scratch repo and torn down) and an
already-running daemon (probed directly, real subprocess, real port)."""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muvue.core import doctor
from muvue.core.repo_init import init_repo


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_throwaway_daemon_probe_passes_and_leaves_repo_untouched(tmp_path: Path):
    """No daemon running anywhere near this port -- doctor must spin
    one up (against a *scratch* repo, not `tmp_path`), probe it, and
    tear it down, all controls passing (this session's own daemon
    correctly rejects every probe)."""
    init_repo(tmp_path)
    before = sorted(p.name for p in (tmp_path / ".muvue").iterdir())

    issues, notes = doctor.run_security_probes(tmp_path, port=_free_port())

    assert issues == [], issues
    assert any("no daemon was already running" in n for n in notes)
    after = sorted(p.name for p in (tmp_path / ".muvue").iterdir())
    assert before == after, "a read-only security probe must never touch the repo it's checking"


def test_run_doctor_wires_in_security_probes_by_default(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, daemon_port=_free_port())
    assert report.ok is True
    assert any("security controls" in w or "daemon" in w for w in report.warnings)


def test_run_doctor_skip_security_probes_flag_skips_them(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not any("daemon was already running" in w or "throwaway" in w for w in report.warnings)


def test_probing_an_already_running_daemon_reuses_it(tmp_path: Path):
    """When a daemon is already up on the given port, doctor probes
    that one directly rather than spinning up a second, throwaway one."""
    init_repo(tmp_path)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(tmp_path), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if "listening on" in line:
                ready = True
                break
        assert ready

        issues, notes = doctor.run_security_probes(tmp_path, port=port)
        assert issues == [], issues
        assert any(f"already running on port {port}" in n for n in notes)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_probes_fail_loudly_if_a_control_regresses(tmp_path: Path, monkeypatch):
    """Regression-proofing the probe mechanism itself: if the daemon
    stops rejecting a bad Host header, `run_security_probes` must
    report an issue (`report.ok` flips False through `run_doctor`),
    not silently pass. Simulated by monkeypatching the probe helper to
    report a 200 where a 403 was expected, rather than actually
    weakening the real middleware (which the rest of this session's
    tests already prove works)."""
    init_repo(tmp_path)

    real_probe = doctor._probe_request

    def _lying_probe(base_url, path, **kwargs):
        if kwargs.get("headers", {}).get("Host") == "evil.example.com":
            return 200, {}
        return real_probe(base_url, path, **kwargs)

    monkeypatch.setattr(doctor, "_probe_request", _lying_probe)
    issues, _ = doctor.run_security_probes(tmp_path, port=_free_port())
    assert any("Host" in issue and "control 2" in issue for issue in issues)
