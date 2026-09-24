"""v4 section 7 / changelog item 10: commit-trailer enforcement moves from
`PreToolUse` string-matching a `Bash` tool call's `git commit` command
(defeated by `git -C`, heredocs, chained commands, aliases, scripts, and
false-positive on any string containing "git commit") to `post-commit`
detection, post-hoc.

Two things change:

1. `PreToolUse` (both `core.claude_hooks.pre_tool_use`, the pre-v4 full-CLI
   decision logic, and `muvue._hook._decide`, the v4 section 4a fast-path
   mirror of it) no longer blocks a `Bash` `git commit` call for a missing
   trailer. This is a deliberate behavior *reduction* -- see the tests
   below and docs/decisions.md #100. The other `PreToolUse` behavior
   (block `Edit`/`Write` with no `in_progress` node, or an
   `awaiting_approval` one) is unchanged.
2. `post-commit` (`core.hooks.handle_post_commit`, via
   `core.drift.flag_general_unattributed_commit`) now flags *every*
   commit whose trailer is missing or doesn't resolve to a real node --
   not just P7's narrower "touches an anchored component" case -- with a
   distinct `unattributed_commit` event, inbox-visible via `GET /inbox`'s
   new `unattributed_commits` list.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import drift, hooks, nodes, projects


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with NO muvue git hooks installed (deliberately --
    see test_drift.py's `repo` fixture docstring for the same reasoning):
    these tests invoke `muvue hook post-commit` explicitly via subprocess
    to exercise the real `handle_post_commit_from_git` path, so an
    installed real `post-commit` shim firing automatically on `git
    commit` too would double-process the same commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


@pytest.fixture
def conn(repo: Path):
    muvue_dir = repo / ".muvue"
    muvue_dir.mkdir(exist_ok=True)
    c = core_db.init_db(muvue_dir / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="trailer relocation test")
    return projects.set_phase(conn, p["id"], "executing")


def _run_post_commit_hook(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "post-commit", str(repo)],
        cwd=repo, capture_output=True, text=True,
    )


# -- 1. real commit, NO trailer at all -> exactly one inbox item + one
#    `unattributed_commit` event -----------------------------------------


def test_commit_with_no_trailer_raises_exactly_one_unattributed_commit_event(conn, repo, project):
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no trailer at all")
    conn.close()

    result = _run_post_commit_hook(repo)
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        events = conn2.execute(
            "SELECT * FROM events WHERE type = 'unattributed_commit'"
        ).fetchall()
        assert len(events) == 1
        assert events[0]["acked_at"] is None  # inbox-visible: unacked

        inbox_visible = conn2.execute(
            "SELECT COUNT(*) c FROM events WHERE type = 'unattributed_commit' "
            "AND acked_at IS NULL"
        ).fetchone()["c"]
        assert inbox_visible == 1
    finally:
        conn2.close()


# -- 2. real commit, correct matching trailer -> NO unattributed_commit event
#    (no false positive on the happy path) --------------------------------


def test_commit_with_correct_matching_trailer_does_not_flag_unattributed(conn, repo, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready"
    )
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"real work\n\nMuvue-Node: {task['id']}\n")
    conn.close()

    result = _run_post_commit_hook(repo)
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        events = conn2.execute(
            "SELECT * FROM events WHERE type = 'unattributed_commit'"
        ).fetchall()
        assert events == []
    finally:
        conn2.close()


# -- 3. real commit, malformed/non-matching trailer (references a node id
#    that doesn't exist) -> also flagged, not silently ignored -----------


def test_commit_with_trailer_referencing_nonexistent_node_is_flagged_unattributed(
    conn, repo, project
):
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bogus trailer\n\nMuvue-Node: 999999\n")
    conn.close()

    result = _run_post_commit_hook(repo)
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        events = conn2.execute(
            "SELECT * FROM events WHERE type = 'unattributed_commit'"
        ).fetchall()
        assert len(events) == 1
        import json

        payload = json.loads(events[0]["payload"])
        assert payload["trailer_node_ids"] == [999999]
    finally:
        conn2.close()


# -- unit-level equivalents (no subprocess), exercising
#    `core.drift.flag_general_unattributed_commit` / `core.hooks.
#    handle_post_commit` directly ------------------------------------------


def test_flag_general_unattributed_commit_fires_with_no_trailer(conn, project):
    event_id = drift.flag_general_unattributed_commit(
        conn, commit_sha="deadbeef", files=["a.py"], node_ids=[], resolved_node_ids=[],
    )
    assert event_id is not None
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    assert row["type"] == "unattributed_commit"
    assert row["acked_at"] is None


def test_flag_general_unattributed_commit_fires_with_unresolved_trailer(conn, project):
    event_id = drift.flag_general_unattributed_commit(
        conn, commit_sha="deadbeef", files=["a.py"], node_ids=[999999], resolved_node_ids=[],
    )
    assert event_id is not None


def test_flag_general_unattributed_commit_is_a_noop_when_trailer_resolved(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready"
    )
    event_id = drift.flag_general_unattributed_commit(
        conn, commit_sha="deadbeef", files=["a.py"], node_ids=[task["id"]],
        resolved_node_ids=[task["id"]],
    )
    assert event_id is None


def test_handle_post_commit_wires_general_unattributed_flag_unconditionally(conn, project):
    """Unlike `flag_unattributed_commit` (P7 drift loop item 2), the
    general check does not require the commit to touch a file any
    tracked component is anchored to -- no components exist in this test
    at all."""
    result = hooks.handle_post_commit(conn, commit_sha="c1", message="no trailer", files=["x.py"])
    assert result["linked_node_ids"] == []
    row = conn.execute(
        "SELECT * FROM events WHERE type = 'unattributed_commit'"
    ).fetchone()
    assert row is not None


# -- inbox surfacing --------------------------------------------------------


def test_inbox_surfaces_unattributed_commit_as_its_own_list(repo, conn, project):
    from fastapi.testclient import TestClient

    from muvue.api import create_app
    from muvue.core.config import MuvueConfig

    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no trailer")
    result = _run_post_commit_hook(repo)
    assert result.returncode == 0, result.stderr

    client = TestClient(create_app(repo, config=MuvueConfig()), base_url="http://127.0.0.1")
    r = client.get("/inbox")
    assert r.status_code == 200
    body = r.json()
    assert len(body["unattributed_commits"]) >= 1
    assert body["unattributed_commits"][0]["type"] == "unattributed_commit"


# -- 5. squash-merge trailer acceptance (v4 P3 acceptance criterion,
#    re-checked for this session's changes): "a squash-merged branch
#    yields exactly one inbox item per lost trailer *or* the trailers
#    survive in the squash body -- assert whichever the fixture produces,
#    do not assume survival." The existing squash test in
#    test_trailers_hooks.py only exercises the "survives" branch; this
#    adds the "lost" branch with a fixture that actually drops the
#    trailers, per the P3 prompt's own suggested mechanism
#    (`git merge --squash --no-commit` + a hand-authored message that
#    does NOT carry the trailers forward). ------------------------------


def test_squash_merge_that_drops_trailers_flags_one_unattributed_commit_per_lost_trailer(
    conn, repo, project
):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready"
    )
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"fix parser\n\nMuvue-Node: {task['id']}\n")
    _git(repo, "checkout", "-q", "main")

    # `--no-commit` stages the squash diff without composing the default
    # concatenated message, so a hand-authored message that omits the
    # trailer produces a fixture that genuinely drops it (unlike
    # `test_trailers_hooks.py`'s squash test, which lets git compose the
    # default message and so keeps every trailer).
    _git(repo, "merge", "-q", "--squash", "--no-commit", "feature")
    _git(repo, "commit", "-q", "-m", "fix parser (squashed, no trailer carried forward)")
    conn.close()

    result = _run_post_commit_hook(repo)
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        events = conn2.execute(
            "SELECT * FROM events WHERE type = 'unattributed_commit'"
        ).fetchall()
        # Exactly one inbox item for this one commit's one lost trailer.
        assert len(events) == 1
    finally:
        conn2.close()
