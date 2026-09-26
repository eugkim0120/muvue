"""`muvue --version` and the top-level `--help` text."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version


def _muvue(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, cwd=cwd,
    )


def test_version_prints_the_installed_version(tmp_path):
    result = _muvue("--version", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"muvue {version('muvue')}"


def test_help_describes_muvue_not_the_queue_drain(tmp_path):
    result = _muvue("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "AI coding agents" in result.stdout
    assert "v4 section" not in result.stdout
    assert "plan section" not in result.stdout
    assert "--version" in result.stdout
