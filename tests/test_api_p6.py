"""P6: `/projects/{id}/close-preview`, `/projects/{id}/close`, `/import`,
and `/nodes/{id}/merge?pr=true` -- the API mirrors the CLI 1:1 (plan
section 4), human verbs gated by session token (plan section 8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import db as core_db, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hi\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial commit")
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def app(repo, config):
    return create_app(repo, config=config)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def token(app) -> str:
    return app.state.session.token


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def done_task(conn):
    project = projects.create_project(conn, goal="api p6 test")
    project = projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="a1")
    task = nodes.done(conn, task["id"], owner="a1")["node"]
    return {"project": project, "task": task}


def test_close_preview_requires_session_token(client, done_task):
    r = client.get(f"/projects/{done_task['project']['id']}/close-preview")
    assert r.status_code == 403


def test_close_preview_shows_closeable_diff(client, done_task, token):
    r = client.get(
        f"/projects/{done_task['project']['id']}/close-preview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["closeable"] is True
    assert "diff" in body


def test_close_commits_and_flips_phase(client, done_task, token):
    r = client.post(
        f"/projects/{done_task['project']['id']}/close",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["phase"] == "closed"
    assert Path(body["components_path"]).exists()
    assert Path(body["history_path"]).exists()


def test_import_requires_session_token(client, done_task):
    r = client.post(
        "/import",
        json={"node_id": done_task["task"]["id"], "issue_number": 1, "data": {}},
    )
    assert r.status_code == 403


def test_import_links_external_ref(client, done_task, token):
    r = client.post(
        "/import",
        json={
            "node_id": done_task["task"]["id"], "issue_number": 5,
            "data": {"number": 5, "title": "x", "url": "https://github.com/o/r/issues/5"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["external_ref"]["ext_id"] == "5"


def test_merge_with_pr_flag_includes_body(client, done_task, token):
    r = client.post(
        f"/nodes/{done_task['task']['id']}/merge?pr=true",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no_worktree"
    assert "## Acceptance criteria" in body["pr_body"]
