"""P5 acceptance criterion 5 (plan section 11 row P5, section 6 "Merging"):
"Daemon merges in dependency order. On conflict: node -> blocked(conflict),
a 'rebase onto main' subtask is auto-created for the same owner,
attempts + 1."

Only meaningful in strict mode (see `src/muvue/core/merge.py`'s module
docstring for why) -- these tests build real strict-mode worktrees and
real git conflicts, the same way `tests/test_strict_mode.py` (P4) does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, merge as merge_mod, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env,
    )


def _init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "shared.txt").write_text("base\n")
    (root / "app.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def repo(tmp_path, home) -> Path:
    return _init_git_repo(tmp_path / "repo")


@pytest.fixture
def strict_config() -> MuvueConfig:
    cfg = MuvueConfig()
    cfg.mode = "strict"
    cfg.worktree_setup = ""
    return cfg


@pytest.fixture
def conn(repo):
    init_repo(repo)
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="merge test")


def _done_strict_task(conn, project, config, repo, *, title, filename, content, depends_on=None):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title=title, status="pending",
    )
    if depends_on is not None:
        conn.execute(
            "INSERT INTO deps (node_id, depends_on) VALUES (?, ?)", (task["id"], depends_on),
        )
        conn.commit()
    gates.approve_gate2(conn, project["id"], config=config)
    started = nodes.start(conn, task["id"], owner=f"agent-{task['id']}", config=config, repo_root=repo)
    wt = Path(started["node"]["worktree"])
    (wt / filename).write_text(content)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"work for {title}")
    nodes.done(conn, task["id"], owner=f"agent-{task['id']}")
    return nodes.get_node(conn, task["id"])


def test_merge_no_worktree_is_a_documented_noop(conn, project):
    """Light-mode (or never-strict-started) nodes have nothing to merge."""
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="a")
    nodes.done(conn, task["id"], owner="a")
    assert merge_mod.attempt_merge(conn, task["id"], Path("/nonexistent"))["status"] == "no_worktree"


def test_clean_merge_succeeds(conn, project, strict_config, repo):
    task = _done_strict_task(
        conn, project, strict_config, repo, title="t1", filename="new.txt", content="hello\n",
    )
    result = merge_mod.attempt_merge(conn, task["id"], repo)
    assert result["status"] == "merged"
    assert result["sha"]
    # idempotent: calling again is a no-op, not a second merge
    again = merge_mod.attempt_merge(conn, task["id"], repo)
    assert again["status"] == "already_merged"


def test_conflict_blocks_node_bumps_attempts_and_creates_rebase_subtask(conn, project, strict_config, repo):
    t1 = _done_strict_task(
        conn, project, strict_config, repo, title="t1", filename="shared.txt", content="from t1\n",
    )
    t2 = _done_strict_task(
        conn, project, strict_config, repo, title="t2", filename="shared.txt", content="from t2 (conflict)\n",
    )
    assert merge_mod.attempt_merge(conn, t1["id"], repo)["status"] == "merged"

    before_attempts = nodes.get_node(conn, t2["id"])["attempts"]
    result = merge_mod.attempt_merge(conn, t2["id"], repo)
    assert result["status"] == "conflict"

    row = nodes.get_node(conn, t2["id"])
    assert row["status"] == "blocked"
    assert row["block_reason"] == "conflict"
    assert row["attempts"] == before_attempts + 1

    subtask = result["subtask"]
    assert subtask["kind"] == "subtask"
    assert subtask["parent_id"] == t2["id"]
    assert subtask["status"] == "ready"
    assert "rebase" in subtask["title"].lower()

    # findable as a child of the conflicting node
    children = conn.execute(
        "SELECT * FROM nodes WHERE parent_id = ? AND deleted_at IS NULL", (t2["id"],)
    ).fetchall()
    assert len(children) == 1
    assert children[0]["id"] == subtask["id"]


def test_merge_respects_dependency_order(conn, project, strict_config, repo):
    t1 = _done_strict_task(
        conn, project, strict_config, repo, title="t1", filename="a.txt", content="a\n",
    )
    t2 = _done_strict_task(
        conn, project, strict_config, repo, title="t2", filename="b.txt", content="b\n",
        depends_on=t1["id"],
    )
    # t2 depends on t1, which hasn't merged yet -> deferred
    deferred = merge_mod.attempt_merge(conn, t2["id"], repo)
    assert deferred["status"] == "deferred"
    assert deferred["pending_deps"] == [t1["id"]]

    assert merge_mod.attempt_merge(conn, t1["id"], repo)["status"] == "merged"
    assert merge_mod.attempt_merge(conn, t2["id"], repo)["status"] == "merged"


def test_merge_pending_walks_all_done_nodes_in_dependency_order(conn, project, strict_config, repo):
    t1 = _done_strict_task(
        conn, project, strict_config, repo, title="t1", filename="a.txt", content="a\n",
    )
    t2 = _done_strict_task(
        conn, project, strict_config, repo, title="t2", filename="b.txt", content="b\n",
        depends_on=t1["id"],
    )
    results = merge_mod.merge_pending(conn, repo, project_id=project["id"])
    outcomes = {r["node_id"]: r["status"] for r in results}
    assert outcomes[t1["id"]] == "merged"
    assert outcomes[t2["id"]] == "merged"


def test_attempt_merge_refuses_a_non_done_node(conn, project, strict_config, repo):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t1", status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=strict_config)
    nodes.start(conn, task["id"], owner="a", config=strict_config, repo_root=repo)
    with pytest.raises(merge_mod.MergeError):
        merge_mod.attempt_merge(conn, task["id"], repo)
