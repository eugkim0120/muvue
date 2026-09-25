"""Dogfood-gate adapter tightening: the `prepare-commit-msg` shim adds
`Muvue-Node: <id>` to a commit made while `.muvue/current_node` names an
`in_progress` node, so linking a commit no longer depends on the agent
remembering the trailer. Driven through real `git commit` calls."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from muvue.core import adapters
from muvue.core import db as core_db
from muvue.core import nodes, projects
from muvue.core.repo_init import init_repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    init_repo(tmp_path)
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "initial")
    return tmp_path


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _node(conn, status: str) -> int:
    pid = projects.create_project(conn, goal="g")["id"]
    projects.set_phase(conn, pid, "executing")
    node = nodes.create_node(conn, project_id=pid, kind="task", title="t", status="ready")
    if status == "in_progress":
        nodes.start(conn, node["id"], owner="agent")
    return node["id"]


def _commit(repo: Path, message: str) -> str:
    (repo / "f.txt").write_text(message)
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "log", "-1", "--pretty=%B")


def test_commit_gets_the_current_in_progress_node_trailer(repo, conn):
    node_id = _node(conn, "in_progress")
    adapters.set_current_node(repo, node_id)
    assert f"Muvue-Node: {node_id}" in _commit(repo, "do the thing")


def test_no_trailer_when_no_current_node(repo, conn):
    _node(conn, "in_progress")
    assert "Muvue-Node" not in _commit(repo, "unrelated change")


def test_no_trailer_when_current_node_is_not_in_progress(repo, conn):
    # A stale current_node file must not label commits with a node that
    # is no longer being worked on.
    node_id = _node(conn, "ready")
    adapters.set_current_node(repo, node_id)
    assert "Muvue-Node" not in _commit(repo, "after the node finished")


def test_existing_trailer_is_left_alone(repo, conn):
    node_id = _node(conn, "in_progress")
    other = _node(conn, "in_progress")
    adapters.set_current_node(repo, node_id)
    message = _commit(repo, f"explicit\n\nMuvue-Node: {other}")
    assert f"Muvue-Node: {other}" in message
    assert f"Muvue-Node: {node_id}" not in message


def test_commit_is_linked_to_the_node(repo, conn):
    from muvue.core import hooks

    node_id = _node(conn, "in_progress")
    adapters.set_current_node(repo, node_id)
    _commit(repo, "linked work")
    hooks.drain_queue(conn, repo)
    rows = conn.execute("SELECT COUNT(*) AS n FROM node_commits WHERE node_id = ?", (node_id,)).fetchone()
    assert rows["n"] == 1
