"""P6: `close` proposes then commits a project's structure diff
(new components/decisions/promoted lessons), writes
.muvue/components.json / .muvue/decisions.json and commits them on
`main`, exports the project's event history to
.muvue/history/<project-id>.jsonl.gz, and sets phase -> closed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from muvue.core import close as close_mod
from muvue.core import db as core_db
from muvue.core import nodes, projects


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
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


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="close test")
    return projects.set_phase(conn, p["id"], "executing")


def _done_task(conn, project, **kw):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", status="ready", **kw,
    )
    nodes.start(conn, task["id"], owner="a1")
    return nodes.done(conn, task["id"], owner="a1")["node"]


def test_preview_close_refuses_when_nodes_not_done(conn, project):
    nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    preview = close_mod.preview_close(conn, project["id"])
    assert preview["closeable"] is False
    assert len(preview["blocking_nodes"]) == 1


def test_preview_close_surfaces_decisions_and_lessons(conn, project):
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")
    nodes.add_note(conn, task_row["id"], kind="lesson",
                    text=json.dumps({"trigger": "flaky ci", "failure": "test flakes",
                                      "do_instead": "retry with backoff", "scope": "t1"}),
                    pinned=True)

    preview = close_mod.preview_close(conn, project["id"])
    assert preview["closeable"] is True
    assert len(preview["diff"]["decisions"]) == 1
    assert "sqlite WAL" in preview["diff"]["decisions"][0]["choice"]
    assert len(preview["diff"]["promoted_lessons"]) == 1
    assert preview["diff"]["promoted_lessons"][0]["choice"] == "retry with backoff"


def test_close_project_dry_run_does_not_mutate(conn, project, repo):
    _done_task(conn, project, title="t1")
    result = close_mod.close_project(conn, project["id"], repo, confirm=False)
    assert result["confirmed"] is False
    assert projects.get_project(conn, project["id"])["phase"] == "executing"
    assert not (repo / ".muvue" / "components.json").exists()


def test_close_project_refuses_non_human(conn, project, repo):
    _done_task(conn, project, title="t1")
    with pytest.raises(close_mod.HumanOnly):
        close_mod.close_project(conn, project["id"], repo, confirm=True, actor="agent")


def test_close_project_confirmed_writes_and_commits(conn, project, repo):
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")

    result = close_mod.close_project(conn, project["id"], repo, confirm=True)

    assert result["confirmed"] is True
    assert result["project"]["phase"] == "closed"
    assert projects.get_project(conn, project["id"])["phase"] == "closed"

    comp_path = Path(result["components_path"])
    dec_path = Path(result["decisions_path"])
    assert comp_path.exists()
    assert dec_path.exists()
    decisions_on_disk = json.loads(dec_path.read_text())
    assert any("sqlite WAL" in (d.get("choice") or "") for d in decisions_on_disk)

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "close" in log.lower()

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".muvue/components.json", ".muvue/decisions.json"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert status.strip() == ""  # committed, nothing left dirty

    history_path = Path(result["history_path"])
    assert history_path.exists()
    assert history_path.name == f"{project['id']}.jsonl.gz"


def test_close_project_caps_diff_size(conn, project, repo, monkeypatch):
    monkeypatch.setattr(close_mod, "MAX_DIFF_ITEMS", 2)
    task_row = _done_task(conn, project, title="t1")
    for i in range(5):
        nodes.add_note(conn, task_row["id"], kind="decision", text=f"decision number {i}")
    preview = close_mod.preview_close(conn, project["id"])
    assert len(preview["diff"]["decisions"]) == 2
