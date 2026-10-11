"""Hidden end-to-end checks for the readinglog suite. They run the real
CLI from the repo under test (BENCH_REPO) with a throwaway log file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ["BENCH_REPO"])


@pytest.fixture
def cli(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "READINGLOG_FILE": str(tmp_path / "log.json")}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "readinglog.cli", *args],
                              cwd=REPO, env=env, capture_output=True, text=True)
    return run


def test_add_shows_in_progress(cli):
    assert cli("add", "Dune", "--pages", "300").returncode == 0
    out = cli("progress")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["Dune: 0/300 pages (0%)"]


def test_log_updates_progress(cli):
    cli("add", "Dune", "--pages", "300")
    assert cli("log", "Dune", "50").returncode == 0
    assert cli("progress").stdout.splitlines() == ["Dune: 50/300 pages (17%)"]


def test_log_unknown_book_fails(cli):
    out = cli("log", "Missing", "5")
    assert out.returncode == 1
    assert "unknown book" in out.stdout


def test_stats_counts_today(cli):
    cli("add", "Dune", "--pages", "300")
    cli("log", "Dune", "50")
    out = cli("stats")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["Pages this week: 50", "Current streak: 1 day"]
