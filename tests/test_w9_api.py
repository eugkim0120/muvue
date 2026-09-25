"""W9 of the v4 delta closure: the node panel's real diff and log tail,
line-anchored spec comments, and the section 8 KPIs that were missing
(touch drift, spend per driver)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core import db as core_db, hooks, nodes, projects
from muvue.core.config import AgentBudgetConfig, AgentConfig, ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo

from conftest import git_init_with_commit

BASE_URL = "http://127.0.0.1"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git_init_with_commit(tmp_path)
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _client(repo, config=None) -> tuple[TestClient, dict]:
    app = create_app(repo, config=config or MuvueConfig(checks=ChecksConfig(test="true", lint="true")))
    headers = {"Authorization": f"Bearer {app.state.session.token}", "Content-Type": "application/json"}
    return TestClient(app, base_url=BASE_URL), headers


def _task(conn, **kw):
    project = projects.create_project(conn, goal="w9")
    return nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready", **kw)


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    env = {**GIT_ENV, "HOME": str(repo)}
    subprocess.run(["git", "add", path], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_diff_is_the_patch_of_the_nodes_commits(repo, conn):
    task = _task(conn)
    message = f"add util\n\nMuvue-Node: {task['id']}"
    sha = _commit(repo, "pkg/util.py", "def f():\n    return 1\n", message)
    hooks.handle_post_commit(conn, commit_sha=sha, message=message, files=["pkg/util.py"])
    _commit(repo, "other.py", "x = 1\n", "unrelated")

    client, _ = _client(repo)
    body = client.get(f"/nodes/{task['id']}/diff").json()
    assert body["source"] == "commits"
    assert "+    return 1" in body["diff"]
    assert "pkg/util.py" in body["diff"]
    assert "other.py" not in body["diff"]


def test_diff_without_commits_or_worktree_is_empty(repo, conn):
    task = _task(conn)
    client, _ = _client(repo)
    body = client.get(f"/nodes/{task['id']}/diff").json()
    assert body["source"] == "none"
    assert body["diff"] == ""


def test_logs_tail_the_driver_output(repo, conn):
    task = _task(conn)
    logs = repo / ".muvue" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{task['id']}.log").write_text("".join(f"line {i}\n" for i in range(500)))

    client, _ = _client(repo)
    r = client.get(f"/nodes/{task['id']}/logs?lines=3")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "line 497\nline 498\nline 499\n"


def test_logs_for_a_node_without_output(repo, conn):
    task = _task(conn)
    client, _ = _client(repo)
    r = client.get(f"/nodes/{task['id']}/logs")
    assert r.status_code == 200
    assert r.text == ""


def test_logs_for_a_missing_node_404(repo):
    client, _ = _client(repo)
    assert client.get("/nodes/999/logs").status_code == 404


def test_spec_comment_anchored_to_a_line(repo, conn):
    project = projects.create_project(conn, goal="w9")
    spec = nodes.create_node(
        conn, project_id=project["id"], kind="spec", title="s", body_md="one\ntwo\nthree",
    )
    client, headers = _client(repo)
    r = client.post(f"/nodes/{spec['id']}/comment", json={"text": "why two?", "line": 2}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["note"]["text"] == "[L2] why two?"


def test_spec_comment_line_out_of_range_is_rejected(repo, conn):
    project = projects.create_project(conn, goal="w9")
    spec = nodes.create_node(conn, project_id=project["id"], kind="spec", title="s", body_md="one")
    client, headers = _client(repo)
    r = client.post(f"/nodes/{spec['id']}/comment", json={"text": "x", "line": 5}, headers=headers)
    assert r.status_code == 409


def test_kpis_touch_drift_and_spend_per_driver(repo, conn):
    a = _task(conn, predicted_touches=["src/*.py"])
    b = _task(conn, predicted_touches=["src/*.py"])
    for node, paths in ((a, ["src/x.py", "src/y.py"]), (b, ["src/z.py", "docs/readme.md"])):
        with core_db.write_txn(conn):
            for path in paths:
                conn.execute("INSERT INTO actual_touches (node_id, path) VALUES (?, ?)", (node["id"], path))

    config = MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"claude": AgentConfig(
            command="claude", cost_model="usd", budget=AgentBudgetConfig(unit="usd", limit=10.0),
        )},
    )
    client, _ = _client(repo, config)
    k = client.get("/kpis").json()
    # a: 0 of 2 outside, b: 1 of 2 outside; mean 0.25.
    assert k["touch_drift"] == pytest.approx(0.25)
    assert k["spend_by_driver"]["claude"]["limit"] == 10.0
    assert k["spend_by_driver"]["claude"]["pct"] == 0.0


def test_diff_of_an_uncommitted_per_node_worktree(repo, conn, monkeypatch):
    from muvue.core import strict

    monkeypatch.setenv("HOME", str(repo / "home"))
    task = _task(conn)
    wt = strict.bind_light_worktree(nodes.get_node(conn, task["id"]), MuvueConfig(worktree_setup=""), repo)
    with core_db.write_txn(conn):
        conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), task["id"]))
    (wt / "tracked.txt").write_text("before\n")
    env = {**GIT_ENV, "HOME": str(repo)}
    subprocess.run(["git", "add", "tracked.txt"], cwd=wt, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=wt, check=True, env=env)
    (wt / "tracked.txt").write_text("after\n")

    client, _ = _client(repo)
    body = client.get(f"/nodes/{task['id']}/diff").json()
    assert body["source"] == "worktree"
    assert "+after" in body["diff"]


def test_graph_has_parent_and_dependency_edges(repo, conn):
    project = projects.create_project(conn, goal="w9")
    spec = nodes.create_node(conn, project_id=project["id"], kind="spec", title="s")
    a = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", parent_id=spec["id"])
    b = nodes.create_node(conn, project_id=project["id"], kind="task", title="b", parent_id=spec["id"])
    other = projects.create_project(conn, goal="elsewhere")
    nodes.create_node(conn, project_id=other["id"], kind="spec", title="x")
    with core_db.write_txn(conn):
        conn.execute("INSERT INTO deps (node_id, depends_on) VALUES (?, ?)", (b["id"], a["id"]))

    client, _ = _client(repo)
    g = client.get(f"/graph?project_id={project['id']}").json()
    assert sorted(n["id"] for n in g["nodes"]) == [spec["id"], a["id"], b["id"]]
    assert {(e["from"], e["to"], e["kind"]) for e in g["edges"]} == {
        (spec["id"], a["id"], "parent"), (spec["id"], b["id"], "parent"), (a["id"], b["id"], "dep"),
    }


def test_inbox_lists_nodes_awaiting_approval(repo, conn):
    task = _task(conn)
    with core_db.write_txn(conn):
        conn.execute("UPDATE nodes SET status = 'awaiting_approval' WHERE id = ?", (task["id"],))
    client, _ = _client(repo)
    assert [n["id"] for n in client.get("/inbox").json()["awaiting_approval"]] == [task["id"]]
