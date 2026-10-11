"""Mission control phase 1: `git worktree list --porcelain` parsing,
ahead/behind and changed paths per branch (cached by sha), and the rows
the Agents tab reads: base branch, stage, bound node, planned files,
live sessions. Real git fixtures throughout."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import db as core_db, gitutil, hooks, nodes, projects, strict as strict_mod, worktrees
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

PORCELAIN = """worktree /r
HEAD aaaa
branch refs/heads/main

worktree /r/wt
HEAD bbbb
detached

worktree /r/locked
HEAD cccc
branch refs/heads/feat
locked reason here

worktree /bare.git
bare
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def _commit(cwd: Path, name: str) -> None:
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}\n")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"add {name}")


@pytest.fixture(autouse=True)
def _fresh_cache():
    gitutil._STATUS_CACHE.clear()
    yield
    gitutil._STATUS_CACHE.clear()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    return Path(os.path.realpath(root))


# -- gitutil ----------------------------------------------------------------


def test_parse_worktree_porcelain():
    assert gitutil.parse_worktree_porcelain(PORCELAIN) == [
        gitutil.Worktree(path="/r", head="aaaa", branch="main"),
        gitutil.Worktree(path="/r/wt", head="bbbb", branch=None, detached=True),
        gitutil.Worktree(path="/r/locked", head="cccc", branch="feat", locked=True),
        gitutil.Worktree(path="/bare.git", head=None, branch=None, bare=True),
    ]


def test_list_worktrees_reads_a_real_repo(repo, tmp_path):
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt"))
    _git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt2"))
    found = gitutil.list_worktrees(repo)
    assert [(os.path.realpath(w.path), w.branch, w.detached) for w in found] == [
        (str(repo), "main", False),
        (os.path.realpath(tmp_path / "wt"), "feat", False),
        (os.path.realpath(tmp_path / "wt2"), None, True),
    ]
    assert all(len(w.head) == 40 for w in found)


def test_list_worktrees_reports_the_git_error(tmp_path):
    with pytest.raises(gitutil.GitError, match=r"^git failed: fatal: not a git repository"):
        gitutil.list_worktrees(tmp_path)


def test_branch_status_counts_changes_and_caches_by_sha(repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    _commit(wt, "f.txt")
    _commit(repo, "m.txt")
    base_sha, tip_sha = gitutil.rev_parse(repo, "main"), gitutil.rev_parse(repo, "feat")
    calls: list[tuple] = []
    real = gitutil._git
    monkeypatch.setattr(gitutil, "_git", lambda root, *args: calls.append(args) or real(root, *args))

    status = gitutil.branch_status(repo, base_sha=base_sha, tip_sha=tip_sha)
    assert (status.ahead, status.behind, status.changed_paths) == (1, 1, ("f.txt",))
    assert status.fork_time <= status.tip_time
    spawned = len(calls)
    assert spawned > 0

    assert gitutil.branch_status(repo, base_sha=base_sha, tip_sha=tip_sha) == status
    assert len(calls) == spawned  # unchanged worktree: no git at all

    _commit(wt, "g.txt")
    moved = gitutil.branch_status(repo, base_sha=base_sha, tip_sha=gitutil.rev_parse(repo, "feat"))
    assert moved.ahead == 2


@pytest.mark.parametrize("kw, stage", [
    ({"ahead": 0, "behind": 0, "node_status": None, "checks_running": False}, "worktree"),
    ({"ahead": 2, "behind": 0, "node_status": None, "checks_running": False}, "commits"),
    ({"ahead": 2, "behind": 0, "node_status": "in_progress", "checks_running": True}, "checks"),
    ({"ahead": 2, "behind": 1, "node_status": "review", "checks_running": False}, "review"),
    ({"ahead": 0, "behind": 3, "node_status": None, "checks_running": False}, "merged"),
    ({"ahead": 0, "behind": 0, "node_status": "done", "checks_running": False}, "merged"),
])
def test_worktree_stage(kw, stage):
    assert worktrees.worktree_stage(**kw) == stage


# -- rows for the Agents tab --------------------------------------------------


def _spool(repo: Path, **line) -> None:
    line.setdefault("ts", _hook._now_iso())
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def test_rows_link_sessions_nodes_planned_files_and_stage(repo, tmp_path):
    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        project = projects.create_project(conn, goal="g")
        node = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="Detect pitch", status="ready",
            predicted_touches=["web/*.ts", "core/*.py"],
        )
        wt = tmp_path / "node-wt"
        _git(repo, "worktree", "add", "-q", "-b", f"node-{node['id']}", str(wt))
        conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
        _commit(wt, "core/pitch.py")
        _git(repo, "worktree", "add", "-q", "-b", "idle", str(tmp_path / "idle-wt"))
        _spool(repo, event="session-start", node_id=None, session_id="s-1", cwd=str(wt / "core"), agent="claude")
        _spool(repo, event="session-start", node_id=None, session_id="s-main", cwd=str(repo), agent="claude")
        hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()

    assert result["errors"] == []
    rows = {r["branch"]: r for r in result["worktrees"]}
    assert (rows["main"]["is_base"], rows["main"]["stage"], rows["main"]["session_ids"]) == (True, None, ["s-main"])
    row = rows[f"node-{node['id']}"]
    assert (row["ahead"], row["behind"], row["changed_paths"], row["stage"]) == (1, 0, ["core/pitch.py"], "commits")
    assert (row["node_id"], row["node_title"], row["session_ids"], row["base"]) == (node["id"], "Detect pitch", ["s-1"], "main")
    assert row["planned"] == [{"glob": "core/*.py", "touched": True}, {"glob": "web/*.ts", "touched": False}]
    assert (rows["idle"]["session_ids"], rows["idle"]["stage"], rows["idle"]["merged"]) == ([], "worktree", False)


def test_rows_include_the_strict_mode_airlock(repo, tmp_path, monkeypatch):
    init_repo(repo)
    airlock = tmp_path / "airlock.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(airlock))
    _git(airlock, "worktree", "add", "-q", "-b", "node-5", str(tmp_path / "node-5"), "main")
    monkeypatch.setattr(strict_mod, "airlock_path", lambda root: airlock)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()
    row = next(r for r in result["worktrees"] if r["branch"] == "node-5")
    assert (row["base"], row["is_base"], row["stage"], row["error"]) == ("main", False, "worktree", None)


def test_a_git_failure_lands_on_its_row(repo, tmp_path, monkeypatch):
    init_repo(repo)
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt"))

    def failing(*args, **kwargs):
        raise gitutil.GitError("git failed: boom")

    monkeypatch.setattr(gitutil, "branch_status", failing)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()
    feat = next(r for r in result["worktrees"] if r["branch"] == "feat")
    assert (feat["error"], feat["stage"]) == ("git failed: boom", None)


def test_a_listing_failure_is_reported_not_raised(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    init_repo(plain)
    conn = core_db.connect(plain / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, plain, MuvueConfig())
    finally:
        conn.close()
    assert result["worktrees"] == []
    assert len(result["errors"]) == 1 and "git failed: " in result["errors"][0]
