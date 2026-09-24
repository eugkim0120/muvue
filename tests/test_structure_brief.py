"""P6 acceptance #1: a NEW project's `brief` cites a prior decision from
a project that has already `close`d, via the FTS5 ranking over
`decisions`/`components` now that `close` populates them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from muvue.core import close as close_mod
from muvue.core import db as core_db
from muvue.core import nodes, projects, queries


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


def _done_task(conn, project, **kw):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", status="ready", **kw)
    nodes.start(conn, task["id"], owner="a1")
    return nodes.done(conn, task["id"], owner="a1")["node"]


def test_brief_surfaces_prior_decision_from_closed_project(conn, repo):
    project_a = projects.create_project(conn, goal="project A: db layer")
    project_a = projects.set_phase(conn, project_a["id"], "executing")
    task_a = _done_task(conn, project_a, title="pick storage engine")
    nodes.add_note(
        conn, task_a["id"], kind="decision",
        text="use sqlite WAL mode for concurrent writes instead of a separate write queue",
    )
    close_mod.close_project(conn, project_a["id"], repo, confirm=True)

    project_b = projects.create_project(conn, goal="project B: reporting")
    project_b = projects.set_phase(conn, project_b["id"], "executing")
    task_b = nodes.create_node(
        conn, project_id=project_b["id"], kind="task",
        title="add concurrent writes to the reporting db",
        body_md="need to handle sqlite concurrency for report writers",
        status="ready",
    )

    brief = queries.brief_node(conn, task_b["id"])
    assert "relevant_decisions" in brief
    titles_and_choices = [
        (d.get("title", ""), d.get("choice", "")) for d in brief["relevant_decisions"]
    ]
    assert any("WAL" in title or "WAL" in choice for title, choice in titles_and_choices)


def test_search_decisions_and_components_direct(conn, repo):
    from muvue.core import events as events_mod

    conn.execute(
        "INSERT INTO decisions (title, context, choice, status) "
        "VALUES ('use sqlite WAL mode', 'concurrency', 'WAL mode', 'current')"
    )
    conn.execute(
        "INSERT INTO components (name, kind, purpose, status) "
        "VALUES ('src/db.py', 'path', 'sqlite connection helpers', 'current')"
    )
    conn.commit()

    decisions = queries.search_decisions(conn, "sqlite WAL concurrency")
    assert decisions and "WAL" in decisions[0]["title"]

    components = queries.search_components(conn, "sqlite connection helpers")
    assert components and components[0]["name"] == "src/db.py"
