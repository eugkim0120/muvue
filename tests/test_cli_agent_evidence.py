"""Agent verbs issued through the CLI record how the caller was
identified, like human verbs do. Found while dogfooding W12: every
`start`/`done`/`spec`/`decompose` from a non-interactive shell was
recorded as `actor_evidence = "tty"`."""

from __future__ import annotations

import subprocess
import sys

from muvue.core import db as core_db
from muvue.core.repo_init import init_repo


def _cli(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args, "--path", str(repo)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )


def test_cli_agent_verbs_without_a_tty_are_not_recorded_as_tty(tmp_path):
    init_repo(tmp_path)
    config = tmp_path / ".muvue" / "config.toml"
    config.write_text(config.read_text().replace('test = "pytest -q"', 'test = "true"').replace(
        'lint = "ruff check ."', 'lint = "true"'))
    for args in (
        ["project", "create", "--goal", "g"],
        ["spec", "1", "--title", "s", "--body", "b"],
    ):
        assert _cli(tmp_path, *args).returncode == 0
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    with core_db.write_txn(conn):
        conn.execute("UPDATE nodes SET status = 'ready' WHERE id = 1")
    for args in (
        ["decompose", "1", "--title", "t", "--criteria", "c", "--criteria-mode", "auto"],
        ["note", "2", "--text", "found something"],
    ):
        result = _cli(tmp_path, *args)
        assert result.returncode == 0, result.stderr
    rows = conn.execute(
        "SELECT type, actor_evidence FROM events WHERE type IN "
        "('project.created', 'node.created', 'note.added') ORDER BY id"
    ).fetchall()
    conn.close()
    assert {r["type"] for r in rows} == {"project.created", "node.created", "note.added"}
    assert all(r["actor_evidence"] != "tty" for r in rows), [tuple(r) for r in rows]
