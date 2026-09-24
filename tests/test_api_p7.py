"""P7: `/kpis`' real `drift_pct`, `/inbox`'s new drift signals, and
`/events`' `since`/`until` timeline-scrubber query params."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import db as core_db, drift, events as events_mod, hooks, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


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
    (tmp_path / "anchored.py").write_text("value = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial commit")
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def client(repo, config) -> TestClient:
    return TestClient(create_app(repo, config=config))


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def test_kpis_drift_pct_is_real_not_stubbed(client, conn, repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    conn.execute(
        "INSERT INTO components (name, kind, purpose, anchors_json, status, verified_sha) "
        "VALUES ('verified', NULL, NULL, '[]', 'current', ?)",
        (head,),
    )
    conn.execute(
        "INSERT INTO components (name, kind, purpose, anchors_json, status, verified_sha) "
        "VALUES ('unverified', NULL, NULL, '[]', 'current', NULL)"
    )
    conn.commit()

    r = client.get("/kpis")
    assert r.status_code == 200
    assert r.json()["drift_pct"] == 0.5


def test_inbox_surfaces_unattributed_commit_signal(client, conn, repo):
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    (repo / "anchored.py").write_text("value = 2\n")
    _git(repo, "add", "-A")
    # `repo` has muvue's post-commit hook installed (`init_repo`), but
    # (v4 section 4a, P0.5) that shim now invokes the stdlib-only
    # `muvue._hook` fast path, which only spools a minimal
    # `{"event": "post-commit", "sha": ...}` line to `.muvue/queue.jsonl`
    # -- the real processing (trailer parsing, unattributed-commit inbox
    # flag) happens later, at drain time. Drain explicitly here rather
    # than relying on the SSE loop's periodic drain, which this
    # `TestClient`-driven test never runs long enough to observe.
    _git(repo, "commit", "-q", "-m", "no trailer")
    hooks.drain_queue(conn, repo)

    r = client.get("/inbox")
    assert r.status_code == 200
    body = r.json()
    assert len(body["signals"]) >= 1
    assert body["signals"][0]["type"] == "inbox.unattributed_commit"


def test_inbox_surfaces_audit_drift_signal(client, conn, repo):
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    drift.run_audit(conn, n=5)

    r = client.get("/inbox")
    assert r.status_code == 200
    body = r.json()
    assert len(body["audit_items"]) >= 1
    assert body["audit_items"][0]["type"] == "inbox.audit_drift_signal"


def test_events_since_until_filters_by_ts_window(client, conn):
    project = projects.create_project(conn, goal="p")
    old_id = events_mod.record_event(
        conn, project_id=project["id"], node_id=None, actor="human",
        type_="marker.old", payload={},
    )
    conn.execute("UPDATE events SET ts = '2000-01-01T00:00:00.000000Z' WHERE id = ?", (old_id,))
    new_id = events_mod.record_event(
        conn, project_id=project["id"], node_id=None, actor="human",
        type_="marker.new", payload={},
    )
    conn.execute("UPDATE events SET ts = '2099-01-01T00:00:00.000000Z' WHERE id = ?", (new_id,))
    conn.commit()

    r = client.get("/events", params={"since": "2050-01-01T00:00:00.000000Z"})
    assert r.status_code == 200
    types = [e["type"] for e in r.json()]
    assert "marker.new" in types
    assert "marker.old" not in types

    r = client.get("/events", params={"until": "2050-01-01T00:00:00.000000Z"})
    types = [e["type"] for e in r.json()]
    assert "marker.old" in types
    assert "marker.new" not in types
