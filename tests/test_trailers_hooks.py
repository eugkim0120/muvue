"""P3 acceptance #3: `Muvue-Node:`/`Refs:` trailer parsing survives a
squash-merge commit message that concatenates multiple original commits'
trailers, and the post-commit hook links every resolvable node."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import hooks, nodes, projects, trailers
from muvue.core.repo_init import init_repo

# A realistic squashed commit message: `git merge --squash` / a GitHub
# "squash and merge" concatenates each original commit's subject+body
# (trailers included) into one message on the target branch.
SQUASHED_MESSAGE = """\
Add validation and fix parser bug (#42)

* Fix bug in parser

Muvue-Node: 5

* Add validation

Refs: 5
Muvue-Node: 9

* Tidy up error messages

Refs: 9, 11
"""


# -- unit: trailer parsing ---------------------------------------------------


def test_parse_node_ids_extracts_all_trailers_from_a_squashed_message():
    assert trailers.parse_node_ids(SQUASHED_MESSAGE) == [5, 9, 11]


def test_parse_node_ids_dedupes_repeated_ids():
    msg = "subject\n\nMuvue-Node: 3\nRefs: 3\n"
    assert trailers.parse_node_ids(msg) == [3]


def test_parse_node_ids_empty_when_no_trailers():
    assert trailers.parse_node_ids("just a plain commit message\n") == []


def test_parse_node_ids_ignores_non_numeric_trailer_values():
    # A future human-readable label ("T3.2") is unresolvable, not an
    # error -- silently skipped (see core/trailers.py docstring).
    assert trailers.parse_node_ids("subject\n\nMuvue-Node: T3.2\n") == []


# -- unit: hooks.handle_post_commit (pure data, no git subprocess) ----------


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="trailers test")


def test_handle_post_commit_links_every_resolvable_node_from_a_squash(conn, project):
    n5 = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    n9 = nodes.create_node(conn, project_id=project["id"], kind="task", title="b", status="ready")
    # rewrite the squashed message's ids to the actual node ids this test created
    message = SQUASHED_MESSAGE.replace("5", str(n5["id"])).replace("9", str(n9["id"])).replace(
        "11", "999999"
    )
    result = hooks.handle_post_commit(
        conn, commit_sha="deadbeef", message=message, files=["a.py", "b.py"]
    )
    assert set(result["linked_node_ids"]) == {n5["id"], n9["id"]}
    assert 999999 in result["unresolved_ids"]

    commits = conn.execute(
        "SELECT node_id, sha FROM node_commits WHERE sha = 'deadbeef' ORDER BY node_id"
    ).fetchall()
    assert {r["node_id"] for r in commits} == {n5["id"], n9["id"]}


def test_handle_post_commit_unknown_node_id_is_unresolved_not_an_error(conn, project):
    result = hooks.handle_post_commit(conn, commit_sha="abc", message="Muvue-Node: 424242\n")
    assert result["linked_node_ids"] == []
    assert result["unresolved_ids"] == [424242]


def test_handle_post_commit_enqueues_anchor_and_staleness_signals(conn, project):
    n = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    hooks.handle_post_commit(conn, commit_sha="c1", message=f"Muvue-Node: {n['id']}\n")
    types = [
        r["type"] for r in conn.execute("SELECT type FROM events ORDER BY id").fetchall()
    ]
    assert "anchor.hash_requested" in types
    assert "staleness.flagged" in types
    assert "commit.linked" in types


def test_handle_post_commit_skips_soft_deleted_nodes(conn, project):
    n = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    nodes.soft_delete(conn, n["id"])
    result = hooks.handle_post_commit(conn, commit_sha="c1", message=f"Muvue-Node: {n['id']}\n")
    assert result["linked_node_ids"] == []
    assert n["id"] in result["unresolved_ids"]


def test_no_commit_events_are_node_prefixed(conn, project):
    """rebuild.rebuild_state treats any node.* event as a full node-row
    snapshot (see core/rebuild.py) -- none of this hook's new event types
    may start with "node." or replay will KeyError, the same pitfall P1's
    node.criteria_edited hit (fixed in this same phase, see
    test_gates.py::test_rebuild_matches_live_after_criteria_edit)."""
    n = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    hooks.handle_post_commit(conn, commit_sha="c1", message=f"Muvue-Node: {n['id']}\n")
    from muvue.core import rebuild

    assert rebuild.diff_state(conn) == {}


# -- integration: real git repo, real squash, real `muvue hook post-commit` --


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=base_env,
    )


def test_post_commit_hook_cli_links_node_from_a_real_squashed_commit(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-q", "-b", "main")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "initial commit")

    init_repo(repo_root)
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="squash test")
    task_a = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    task_b = nodes.create_node(conn, project_id=project["id"], kind="task", title="b", status="ready")
    conn.close()

    # Two feature-branch commits, each carrying its own trailer...
    _git(repo_root, "checkout", "-q", "-b", "feature")
    (repo_root / "a.py").write_text("a = 1\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", f"fix parser\n\nMuvue-Node: {task_a['id']}\n")
    (repo_root / "b.py").write_text("b = 1\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", f"add validation\n\nRefs: {task_a['id']}\nMuvue-Node: {task_b['id']}\n")

    # ...squashed onto main into a single commit, the way `git merge
    # --squash` / GitHub's "squash and merge" produce one, concatenating
    # both original messages/trailers into the new commit's message.
    _git(repo_root, "checkout", "-q", "main")
    squashed_message = (
        f"Fix parser and add validation (#1)\n\n"
        f"* fix parser\n\nMuvue-Node: {task_a['id']}\n\n"
        f"* add validation\n\nRefs: {task_a['id']}\nMuvue-Node: {task_b['id']}\n"
    )
    _git(repo_root, "merge", "-q", "--squash", "feature")
    _git(repo_root, "commit", "-q", "-m", squashed_message)

    result = subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "post-commit", str(repo_root)],
        cwd=repo_root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    try:
        linked = {
            r["node_id"]
            for r in conn.execute("SELECT DISTINCT node_id FROM node_commits").fetchall()
        }
    finally:
        conn.close()
    assert linked == {task_a["id"], task_b["id"]}
