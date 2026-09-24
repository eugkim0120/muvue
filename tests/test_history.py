"""P6 acceptance #3: `close` exports a project's events to
.muvue/history/<project-id>.jsonl.gz; replaying only that archive
reproduces the project's final state (project row + nodes) exactly as
it was live right before archiving.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from muvue.core import close as close_mod
from muvue.core import db as core_db
from muvue.core import history, nodes, projects, rebuild


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hi\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    (root / ".muvue").mkdir()
    return root


@pytest.fixture
def conn(repo: Path):
    c = core_db.init_db(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def test_export_project_writes_gzip_jsonl(conn, repo):
    project = projects.create_project(conn, goal="history test")
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a1")
    nodes.done(conn, task["id"], owner="a1")

    out = history.export_project(conn, project["id"], repo)
    assert out == repo / ".muvue" / "history" / f"{project['id']}.jsonl.gz"
    with gzip.open(out, "rt") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) >= 4  # project.created, project.phase_changed, node.created, node.start, node.review, node.done...
    assert all(ev["project_id"] == project["id"] for ev in lines)


def test_rebuild_from_archive_matches_live_state_after_close(conn, repo):
    project = projects.create_project(conn, goal="history test 2")
    project = projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a1")
    nodes.done(conn, task["id"], owner="a1")

    result = close_mod.close_project(conn, project["id"], repo, confirm=True)
    archive = Path(result["history_path"])

    replayed = history.rebuild_from_archive(archive)
    live = rebuild.live_state(conn)

    assert replayed["projects"][project["id"]]["phase"] == "closed"
    assert replayed["projects"][project["id"]] == live["projects"][project["id"]]
    assert replayed["nodes"][task["id"]] == live["nodes"][task["id"]]


def test_diff_project_from_archive_reports_no_mismatch(conn, repo):
    project = projects.create_project(conn, goal="history test 3")
    project = projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a1")
    nodes.done(conn, task["id"], owner="a1")

    result = close_mod.close_project(conn, project["id"], repo, confirm=True)
    archive = Path(result["history_path"])

    mismatches = rebuild.diff_project_from_archive(conn, project["id"], archive)
    assert mismatches == {}
