"""v4 section 5, changelog item 12: branch coherence. "`doctor` and every
`start` compare current `HEAD` against the branch recorded at project
start. On divergence: warn in light mode, refuse in strict mode."

Uses a real git repo (not just the DB) since the check's whole point is
comparing `projects.branch` (recorded from git at project-creation time)
against the repo's *actual*, live current branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, gitutil, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.nodes import NodeError


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
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
    _git(root, "init", "-q", "-b", "main")
    (root / "f.py").write_text("value = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


@pytest.fixture
def conn(repo: Path):
    db_dir = repo / ".muvue-test"
    db_dir.mkdir(exist_ok=True)
    c = core_db.init_db(db_dir / "muvue.db")
    yield c
    c.close()


def _ready_task(conn, project, config):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto", predicted_touches=["a.py"],
        status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


# -- core.gitutil.current_branch --------------------------------------------


def test_current_branch_reads_the_real_checked_out_branch(repo):
    assert gitutil.current_branch(repo) == "main"
    _git(repo, "checkout", "-q", "-b", "feature")
    assert gitutil.current_branch(repo) == "feature"


def test_current_branch_none_outside_a_git_repo(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert gitutil.current_branch(not_a_repo) is None


# -- core.projects.create_project records the branch ------------------------


def test_create_project_records_current_branch(conn, repo):
    project = projects.create_project(conn, goal="p", repo_root=repo)
    assert project["branch"] == "main"


def test_create_project_branch_null_without_repo_root(conn):
    project = projects.create_project(conn, goal="p")
    assert project["branch"] is None


# -- core.nodes.start: light mode warns, proceeds ----------------------------


def test_start_light_mode_warns_on_divergence_but_succeeds(conn, repo):
    config = MuvueConfig()  # mode="light"
    project = projects.create_project(conn, goal="p", repo_root=repo)
    task = _ready_task(conn, project, config)

    _git(repo, "checkout", "-q", "-b", "unrelated-branch")

    result = nodes.start(conn, task["id"], owner="a1", config=config, repo_root=repo)
    assert result["node"]["status"] == "in_progress"
    ev = conn.execute(
        "SELECT * FROM events WHERE type = 'branch.diverged' AND node_id = ?", (task["id"],)
    ).fetchone()
    assert ev is not None
    assert ev["actor"] == "hook"


def test_start_light_mode_no_warning_when_branch_unchanged(conn, repo):
    config = MuvueConfig()
    project = projects.create_project(conn, goal="p", repo_root=repo)
    task = _ready_task(conn, project, config)

    result = nodes.start(conn, task["id"], owner="a1", config=config, repo_root=repo)
    assert result["node"]["status"] == "in_progress"
    ev = conn.execute(
        "SELECT * FROM events WHERE type = 'branch.diverged' AND node_id = ?", (task["id"],)
    ).fetchone()
    assert ev is None


# -- core.nodes.start: strict mode refuses -----------------------------------


def test_start_strict_mode_refuses_on_divergence(conn, repo):
    config = MuvueConfig(mode="strict", worktree_setup="")
    project = projects.create_project(conn, goal="p", repo_root=repo)
    task = _ready_task(conn, project, config)

    _git(repo, "checkout", "-q", "-b", "unrelated-branch")

    with pytest.raises(NodeError, match="branch coherence"):
        nodes.start(conn, task["id"], owner="a1", config=config, repo_root=repo)
    # refused before any worktree bind / status change.
    assert nodes.get_node(conn, task["id"])["status"] == "ready"
    assert nodes.get_node(conn, task["id"])["worktree"] is None


def test_start_strict_mode_no_divergence_binds_worktree_normally(conn, repo):
    config = MuvueConfig(mode="strict", worktree_setup="")
    project = projects.create_project(conn, goal="p", repo_root=repo)
    task = _ready_task(conn, project, config)

    result = nodes.start(conn, task["id"], owner="a1", config=config, repo_root=repo)
    assert result["node"]["status"] == "in_progress"
    assert result["node"]["worktree"] is not None


# -- doctor -------------------------------------------------------------


def test_doctor_warns_on_divergence_in_light_mode(repo):
    from muvue.core.doctor import run_doctor
    from muvue.core.repo_init import init_repo

    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="p", repo_root=repo)
    conn.commit()
    conn.close()

    _git(repo, "checkout", "-q", "-b", "unrelated-branch")

    report = run_doctor(repo, skip_security_probes=True)
    assert report.ok
    assert any("branch coherence" in w for w in report.warnings)
    assert not any("branch coherence" in i for i in report.issues)


def test_doctor_fails_on_divergence_in_strict_mode(repo):
    from muvue.core.doctor import run_doctor
    from muvue.core.repo_init import init_repo

    init_repo(repo)
    config_path = repo / ".muvue" / "config.toml"
    config_path.write_text(config_path.read_text().replace('mode = "light"', 'mode = "strict"'))
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="p", repo_root=repo)
    conn.commit()
    conn.close()

    _git(repo, "checkout", "-q", "-b", "unrelated-branch")

    report = run_doctor(repo, skip_security_probes=True)
    assert not report.ok
    assert any("branch coherence" in i for i in report.issues)


def test_doctor_clean_when_no_divergence(repo):
    from muvue.core.doctor import run_doctor
    from muvue.core.repo_init import init_repo

    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="p", repo_root=repo)
    conn.commit()
    conn.close()

    report = run_doctor(repo, skip_security_probes=True)
    assert report.ok
    assert not any("branch coherence" in w for w in report.warnings)
    assert not any("branch coherence" in i for i in report.issues)
