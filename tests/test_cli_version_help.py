"""`muvue --version` prints the installed version; top-level `--help`
shows a one-line description rather than the drain callback's internal
docstring."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args], capture_output=True, text=True,
    )


def test_version_flag_prints_installed_version():
    result = _run("--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"muvue {version('muvue')}"


def test_top_level_help_hides_drain_callback_docstring():
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "section 4a" not in result.stdout
    assert "drain" not in result.stdout
