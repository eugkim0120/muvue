"""`muvue --version` and the top-level `--help` text."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

from conftest import plain


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
    out = plain(result.stdout)
    assert "AI coding agents" in out
    assert "v4 section" not in out
    assert "plan section" not in out
    assert "--version" in out


def test_no_command_or_option_help_cites_the_design_plan():
    import re

    import typer.main

    from muvue.cli.main import app

    cites_plan = re.compile(r"plan section|v4 section|\bP\d\b", re.I)
    found = []

    def walk(group, prefix=""):
        for name, command in getattr(group, "commands", {}).items():
            texts = [command.help or ""] + [getattr(p, "help", "") or "" for p in command.params]
            found.extend(f"{prefix}{name}: {t}" for t in texts if cites_plan.search(t))
            walk(command, prefix + name + " ")

    walk(typer.main.get_command(app))
    assert found == []
