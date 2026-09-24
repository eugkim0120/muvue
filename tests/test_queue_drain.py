"""v4 section 4a: "Queue drain is bounded: the daemon drains
continuously; absent a daemon, the next CLI call drains at most 200
items or 200 ms, whichever comes first, then leaves the rest." P0.5
acceptance #3: "drain stops at the bound with queue non-empty."

Leans on the item-count bound (deterministic) per the phase brief;
the wall-clock bound gets one sanity-check test with an injected slow
processor instead of trying to make real 200ms timing deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import hooks
from muvue.core.repo_init import init_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _spool(repo: Path, n: int, event: str = "stop") -> None:
    path = repo / ".muvue" / "queue.jsonl"
    with path.open("a") as f:
        for i in range(n):
            f.write(json.dumps({"event": event, "ts": f"t{i}", "node_id": None}) + "\n")


def _queue_line_count(repo: Path) -> int:
    path = repo / ".muvue" / "queue.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def test_drain_stops_at_item_bound_leaving_the_rest_queued(repo, conn):
    _spool(repo, 250)

    result = hooks.drain_queue(conn, repo, max_items=200, max_seconds=10)

    assert result["drained"] == 200
    assert result["remaining"] == 50
    assert _queue_line_count(repo) == 50


def test_drain_processes_everything_when_under_the_bound(repo, conn):
    _spool(repo, 5)

    result = hooks.drain_queue(conn, repo, max_items=200, max_seconds=10)

    assert result["drained"] == 5
    assert result["remaining"] == 0
    assert _queue_line_count(repo) == 0


def test_drain_is_a_noop_with_no_queue_file(repo, conn):
    result = hooks.drain_queue(conn, repo)
    assert result == {"drained": 0, "remaining": 0}


def test_second_drain_call_continues_from_where_the_first_left_off(repo, conn):
    _spool(repo, 250)

    first = hooks.drain_queue(conn, repo, max_items=200, max_seconds=10)
    second = hooks.drain_queue(conn, repo, max_items=200, max_seconds=10)

    assert first["drained"] == 200
    assert second["drained"] == 50
    assert second["remaining"] == 0
    assert _queue_line_count(repo) == 0


def test_drain_stops_at_the_wall_clock_bound(repo, conn, monkeypatch):
    _spool(repo, 250)

    # Deterministic clock: each `_process_queue_event` call advances the
    # clock past the 200ms budget after 3 items, without real sleeping.
    calls = {"n": 0}
    times = iter([0.0, 0.05, 0.10, 0.30] + [1.0] * 300)

    def fake_clock():
        return next(times)

    monkeypatch.setattr(hooks, "_drain_clock", fake_clock)

    result = hooks.drain_queue(conn, repo, max_items=200, max_seconds=0.2)

    assert 0 < result["drained"] < 200
    assert result["remaining"] == 250 - result["drained"]


def _git(repo: Path, *args: str) -> str:
    import subprocess

    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
        },
    )
    return out.stdout


def test_drain_processes_post_commit_through_the_existing_full_handler(repo, conn):
    """The queue line the fast path spools only carries the sha (v4
    section 4a: "everything else about that commit's diff can be
    re-derived from git when the queue is drained") -- drain must
    re-derive the trailer/files from a *real* git repo via
    `core.hooks.handle_post_commit_from_git_sha`, the same full handler
    `muvue hook post-commit` always used, not a reimplementation."""
    from muvue.core import nodes, projects

    project = projects.create_project(conn, goal="drain test")
    node = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready"
    )

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "initial commit")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"fix\n\nMuvue-Node: {node['id']}\n")
    sha = _git(repo, "rev-parse", "HEAD").strip()

    path = repo / ".muvue" / "queue.jsonl"
    path.write_text(json.dumps({"event": "post-commit", "ts": "t0", "sha": sha}) + "\n")

    result = hooks.drain_queue(conn, repo)

    assert result["drained"] == 1
    row = conn.execute(
        "SELECT * FROM node_commits WHERE node_id = ? AND sha = ?", (node["id"], sha)
    ).fetchone()
    assert row is not None
    assert "a.py" in json.loads(row["files"])
