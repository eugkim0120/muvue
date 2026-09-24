"""v4 section 4a: "absent a daemon, the next CLI call drains at most
200 items or 200 ms" -- `cli/main.py`'s `@app.callback()` is the "next
CLI call" catch-up point. Drives it through a real subprocess CLI
invocation (no raw `core.*` calls), matching this repo's existing
CLI-integration test convention (see tests/test_planning_cli.py).

`--skip-security-probes` throughout: v4 section 8a control 7 added a
live daemon-security probe pass to `muvue doctor` (a real, if scratch,
`muvue serve` subprocess spin-up when nothing is already listening --
see tests/test_doctor_security_probes.py), which is orthogonal to the
bounded-drain behavior this file tests and would otherwise slow every
test here down for no assertion benefit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from muvue.core.repo_init import init_repo


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        cwd=repo_root, capture_output=True, text=True,
    )


def _spool(repo_root: Path, n: int) -> None:
    path = repo_root / ".muvue" / "queue.jsonl"
    with path.open("a") as f:
        for i in range(n):
            f.write(json.dumps({"event": "stop", "ts": f"t{i}", "node_id": None}) + "\n")


def test_any_cli_command_drains_the_queue_first(tmp_path: Path):
    init_repo(tmp_path)
    _spool(tmp_path, 5)

    result = _run(tmp_path, "doctor", "--skip-security-probes")

    assert result.returncode == 0, result.stderr
    queue_path = tmp_path / ".muvue" / "queue.jsonl"
    assert not queue_path.exists() or queue_path.read_text().strip() == ""


def test_drain_callback_is_bounded_at_200(tmp_path: Path):
    init_repo(tmp_path)
    _spool(tmp_path, 250)

    result = _run(tmp_path, "doctor", "--skip-security-probes")

    assert result.returncode == 0, result.stderr
    queue_path = tmp_path / ".muvue" / "queue.jsonl"
    remaining = [ln for ln in queue_path.read_text().splitlines() if ln.strip()]
    assert len(remaining) == 50


def test_drain_failure_never_breaks_the_command(tmp_path: Path):
    """A malformed queue.jsonl line is dropped, not fatal to the
    command it's a prefix to (`doctor` still runs, still exits 0)."""
    init_repo(tmp_path)
    (tmp_path / ".muvue" / "queue.jsonl").write_text("not json\n")

    result = _run(tmp_path, "doctor", "--skip-security-probes")

    assert result.returncode == 0, result.stderr


def test_command_with_no_muvue_dir_is_unaffected(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "--help"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0
