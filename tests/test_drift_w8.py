"""v4 section 9 deltas (W8): approval re-verifies stale components the
node touched; `close` proposes anchored components from real touches
plus changed components; `audit` drafts a real diff; lesson decay is
"not retrieved in the last K projects"."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from muvue.core import close, db as core_db, drift, hooks, nodes, projects
from muvue.core.config import ChecksConfig, MuvueConfig


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "anchored.py").write_text("value = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    (root / ".muvue").mkdir()
    return root


@pytest.fixture
def conn(repo):
    c = core_db.init_db(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig(checks=ChecksConfig(test="true", lint="true"))


def _commit(repo: Path, conn, node_id: int, files: dict[str, str | None]) -> str:
    for name, content in files.items():
        if content is None:
            (repo / name).unlink()
        else:
            (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"work\n\nMuvue-Node: {node_id}")
    sha = _git(repo, "rev-parse", "HEAD")
    hooks.handle_post_commit(conn, commit_sha=sha, message=f"w\n\nMuvue-Node: {node_id}",
                             files=list(files))
    drift.mark_stale_for_commit(conn, repo, sha, list(files))
    return sha


def _started(conn, *, touches):
    project = projects.create_project(conn, goal="w8")
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="loader work",
                             status="ready", criteria=["ok"], criteria_mode="auto",
                             predicted_touches=touches)
    nodes.start(conn, task["id"], owner="agent-1")
    return project, task


def test_review_approval_reverifies_the_stale_components_it_touched(conn, repo, config):
    component = drift.create_anchored_component(conn, name="anchored", file_path="anchored.py",
                                                repo_root=repo)
    project, task = _started(conn, touches=["anchored.py"])
    sha = _commit(repo, conn, task["id"], {"anchored.py": "value = 2\n"})
    assert drift_status(conn, component["id"]) == "stale"

    assert nodes.done(conn, task["id"], owner="agent-1", config=config)["node"]["status"] == "review"
    nodes.approve_review(conn, task["id"], repo_root=repo)

    row = core_db.query_one(conn, "SELECT * FROM components WHERE id = ?", (component["id"],))
    assert row["status"] == "current"
    assert row["verified_sha"] == sha
    assert json.loads(row["anchors_json"])["anchored.py"] == drift.blob_hash(repo, sha, "anchored.py")


def drift_status(conn, component_id):
    return core_db.query_one(conn, "SELECT status FROM components WHERE id = ?",
                             (component_id,))["status"]


def test_close_proposes_anchored_components_from_real_touches_and_changed_ones(conn, repo, config):
    existing = drift.create_anchored_component(conn, name="anchored", file_path="anchored.py",
                                               repo_root=repo)
    project, task = _started(conn, touches=["pricing/*", "anchored.py"])
    (repo / "pricing").mkdir()
    sha = _commit(repo, conn, task["id"], {"pricing/loader.py": "def load(): ...\n",
                                            "anchored.py": "value = 3\n"})
    nodes.done(conn, task["id"], owner="agent-1", config=None)

    diff = close.preview_close(conn, project["id"])["diff"]
    assert [c["name"] for c in diff["components"]] == ["pricing/loader.py"]
    assert diff["components"][0]["file_path"] == "pricing/loader.py"
    assert [c["id"] for c in diff["changed_components"]] == [existing["id"]]

    close.close_project(conn, project["id"], repo, confirm=True)
    new = core_db.query_one(conn, "SELECT * FROM components WHERE name = 'pricing/loader.py'")
    assert json.loads(new["anchors_json"]) == {
        "pricing/loader.py": drift.blob_hash(repo, sha, "pricing/loader.py")}
    assert new["verified_sha"] == sha
    changed = core_db.query_one(conn, "SELECT * FROM components WHERE id = ?", (existing["id"],))
    assert changed["status"] == "current" and changed["verified_sha"] == sha


def test_audit_drafts_a_real_diff_for_a_drifted_component(conn, repo):
    component = drift.create_anchored_component(conn, name="anchored", file_path="anchored.py",
                                                repo_root=repo)
    (repo / "anchored.py").write_text("value = 2\n")
    _git(repo, "commit", "-qam", "hand edit")
    head = _git(repo, "rev-parse", "HEAD")
    result = drift.run_audit(conn, n=5, repo_root=repo)
    item = result["drafted"][0]
    assert item["component_id"] == component["id"]
    payload = json.loads(core_db.query_one(
        conn, "SELECT payload FROM events WHERE id = ?", (item["event_id"],))["payload"])
    assert "-value = 1" in payload["diff"] and "+value = 2" in payload["diff"]
    assert payload["proposed"] == {
        "anchors": {"anchored.py": drift.blob_hash(repo, head, "anchored.py")},
        "verified_sha": head,
    }


def _lesson(conn):
    project, task = _started(conn, touches=["x.py"])
    nodes.fail(conn, task["id"], owner="agent-1", lesson="floats lost cents", trigger="t",
               do_instead="use cents", scope="x.py")
    return project, core_db.query_one(conn, "SELECT id FROM notes WHERE kind = 'lesson'")["id"]


def test_a_new_lesson_is_not_archived(conn):
    _, note_id = _lesson(conn)
    assert drift.decay_lessons(conn, k=3) == []


def test_lesson_unused_in_the_last_k_projects_is_archived(conn):
    _, note_id = _lesson(conn)
    later = [projects.create_project(conn, goal=f"p{i}") for i in range(3)]
    assert drift.decay_lessons(conn, k=3) == [note_id]


def test_lesson_used_in_one_of_the_last_k_projects_is_kept(conn):
    _, note_id = _lesson(conn)
    later = [projects.create_project(conn, goal=f"p{i}") for i in range(3)]
    drift.record_lesson_retrieval(conn, note_id, later[1]["id"])
    assert drift.decay_lessons(conn, k=3) == []
