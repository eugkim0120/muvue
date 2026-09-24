"""P7 drift loop (plan section 9): anchors, post-commit staleness
marking, unattributed-commit inbox signals, reconcile-on-touch, `audit`,
the real `drift_pct` KPI, and lesson decay.

Rebuild test goes first (working rule: every new mutating flow gets a
rebuild test) -- P7's mutation is `components.status`/`anchors_json`
flipping via `component.updated` events, and `notes.archived_at` via
`note.archived` events.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import drift, hooks, nodes, projects, queries, rebuild, review
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with no muvue git hooks installed -- most of this
    module tests `core.drift`/`core.hooks` functions directly, so a real
    `git commit` here must *not* itself trigger an automatic
    `post-commit` hook re-doing the work under test (that would make
    e.g. `hooks.handle_post_commit_from_git`'s own return value look
    like a no-op even though it did the real work). The one test that
    wants the real installed-hook chain (the P7 acceptance-criterion-1
    test) uses `hooked_repo` instead."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "anchored.py").write_text("value = 1\n")
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
def hooked_repo(tmp_path: Path) -> Path:
    """Same as `repo`, but with muvue's git hook shims actually installed
    (`init_repo`) -- for the one test that exercises the real
    `git commit` -> installed `post-commit` shim -> `muvue hook
    post-commit` chain end to end (plan P7 acceptance #1)."""
    root = tmp_path / "hooked_repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "anchored.py").write_text("value = 1\n")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    init_repo(root)
    return root


@pytest.fixture
def hooked_conn(hooked_repo: Path):
    c = core_db.connect(hooked_repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def hooked_project(hooked_conn):
    return projects.create_project(hooked_conn, goal="drift test (hooked)")


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="drift test")
    return projects.set_phase(conn, p["id"], "executing")


# -- rebuild coverage (written first) ---------------------------------------


def test_rebuild_replays_component_staleness(conn, repo, project):
    component = drift.create_anchored_component(
        conn, name="anchored", file_path="anchored.py", repo_root=repo,
    )
    (repo / "anchored.py").write_text("value = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "hand edit")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    drift.mark_stale_for_commit(conn, repo, sha, ["anchored.py"])
    conn.commit()

    live = conn.execute(
        "SELECT status FROM components WHERE id = ?", (component["id"],)
    ).fetchone()
    assert live["status"] == "stale"
    assert rebuild.diff_state(conn) == {}


def test_rebuild_replays_lesson_archival(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(
        conn, task["id"], owner="a", lesson="l", trigger="t", do_instead="d", scope="s",
    )
    drift.decay_lessons(conn, k=1)
    assert rebuild.diff_state(conn) == {}


# -- acceptance criterion 1: hand edit marks component stale within one commit --


def test_hand_edit_to_anchored_file_marks_component_stale_within_one_commit(conn, repo, project):
    component = drift.create_anchored_component(
        conn, name="anchored", file_path="anchored.py", repo_root=repo,
    )
    assert component["status"] == "current"

    (repo / "anchored.py").write_text("value = 2  # hand edit, not through muvue\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "hand edit anchored.py")
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "post-commit", str(repo)],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    conn2 = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        row = conn2.execute(
            "SELECT status, anchors_json FROM components WHERE id = ?", (component["id"],)
        ).fetchone()
        assert row["status"] == "stale"
        anchors = json.loads(row["anchors_json"])
        assert anchors["anchored.py"] != component["anchors_json"]
    finally:
        conn2.close()


def test_pure_rename_retargets_anchor_without_marking_stale(conn, repo, project):
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    _git(repo, "mv", "anchored.py", "renamed.py")
    _git(repo, "commit", "-q", "-m", "rename")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    files_raw = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout
    files = [f for f in files_raw.splitlines() if f]

    newly_stale = drift.mark_stale_for_commit(conn, repo, sha, files)
    conn.commit()
    assert newly_stale == []

    row = conn.execute("SELECT status, anchors_json FROM components").fetchone()
    assert row["status"] == "current"
    assert "renamed.py" in json.loads(row["anchors_json"])
    assert "anchored.py" not in json.loads(row["anchors_json"])


def test_content_change_via_muvue_hooks_module_unit(conn, repo, project):
    """Unit-level (no CLI subprocess) equivalent of the acceptance test,
    exercising `hooks.handle_post_commit_from_git` directly."""
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    (repo / "anchored.py").write_text("value = 999\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "another hand edit")

    result = hooks.handle_post_commit_from_git(conn, repo)
    assert result["newly_stale_component_ids"]

    row = conn.execute("SELECT status FROM components").fetchone()
    assert row["status"] == "stale"


# -- drift loop item 2: unattributed commit touching an anchor -> inbox -----


def test_unattributed_commit_touching_anchor_flags_inbox_signal(conn, repo, project):
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    (repo / "anchored.py").write_text("value = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no trailer here")

    hooks.handle_post_commit_from_git(conn, repo)

    signal = conn.execute(
        "SELECT * FROM events WHERE type = 'inbox.unattributed_commit'"
    ).fetchone()
    assert signal is not None
    payload = json.loads(signal["payload"])
    assert "anchored.py" in payload["files"]
    assert signal["acked_at"] is None


def test_attributed_commit_does_not_flag_inbox_signal(conn, repo, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    (repo / "anchored.py").write_text("value = 4\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"edit\n\nMuvue-Node: {task['id']}\n")

    hooks.handle_post_commit_from_git(conn, repo)

    signal = conn.execute(
        "SELECT * FROM events WHERE type = 'inbox.unattributed_commit'"
    ).fetchone()
    assert signal is None


# -- drift loop item 3: reconcile-on-touch blocks done on stale component ---


def test_done_is_flagged_when_touching_a_stale_component(conn, repo, project):
    projects.set_phase(conn, project["id"], "executing")
    component = drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    conn.execute("UPDATE components SET status = 'stale' WHERE id = ?", (component["id"],))
    conn.commit()

    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria=["passes"], criteria_mode="auto",
    )
    conn.execute(
        "INSERT INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
        (task["id"], "anchored.py"),
    )
    conn.commit()
    nodes.start(conn, task["id"], owner="alice")

    config = MuvueConfig()
    result = nodes.done(conn, task["id"], owner="alice", config=config)
    assert result["auto_approved"] is False
    live = nodes.get_node(conn, task["id"])
    assert live["status"] == "review"

    types = [r["type"] for r in conn.execute("SELECT type FROM events ORDER BY id").fetchall()]
    assert "review.stale_component_touched" in types


def test_done_not_flagged_when_component_current(conn, repo, project):
    projects.set_phase(conn, project["id"], "executing")
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)

    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
        criteria=["passes"], criteria_mode="auto",
    )
    conn.execute(
        "INSERT INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
        (task["id"], "anchored.py"),
    )
    conn.commit()
    nodes.start(conn, task["id"], owner="alice")

    config = MuvueConfig()
    result = nodes.done(conn, task["id"], owner="alice", config=config)
    assert result["auto_approved"] is True


# -- acceptance criterion 2: `audit` drafts an inbox item on known drift ----


def test_audit_drafts_inbox_item_for_stale_unverified_component(conn, repo, project):
    drift.create_anchored_component(conn, name="anchored", file_path="anchored.py", repo_root=repo)
    conn.execute("UPDATE components SET verified_sha = NULL WHERE 1=1")
    conn.commit()

    result = drift.run_audit(conn, n=5)
    assert len(result["drafted"]) >= 1

    items = conn.execute(
        "SELECT * FROM events WHERE type = 'inbox.audit_drift_signal'"
    ).fetchall()
    assert len(items) >= 1
    payload = json.loads(items[0]["payload"])
    assert payload["component_id"] is not None
    assert items[0]["acked_at"] is None


def test_audit_samples_never_verified_before_verified(conn, repo, project):
    c1 = drift.create_anchored_component(conn, name="a1", file_path="anchored.py", repo_root=repo)
    conn.execute("UPDATE components SET verified_sha = 'somesha' WHERE id = ?", (c1["id"],))
    cur = conn.execute(
        "INSERT INTO components (name, kind, purpose, anchors_json, status) "
        "VALUES ('a2', 'path', '', '[]', 'current')"
    )
    conn.commit()
    never_verified_id = cur.lastrowid

    result = drift.run_audit(conn, n=1)
    assert result["drafted"][0]["component_id"] == never_verified_id


# -- drift loop item 5: drift_pct KPI ----------------------------------------


def test_drift_pct_is_zero_with_no_components(conn, repo):
    assert drift.drift_pct(conn, repo) == 0.0


def test_drift_pct_counts_components_verified_within_window(conn, repo, project):
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
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
    pct = drift.drift_pct(conn, repo, k=10)
    assert pct == 0.5


# -- lesson decay -------------------------------------------------------------


def test_lesson_retrieved_by_enough_projects_is_not_decayed(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(conn, task["id"], owner="a", lesson="keep me", trigger="t", do_instead="d", scope="s")
    note = conn.execute("SELECT id FROM notes WHERE kind = 'lesson'").fetchone()

    other_projects = [projects.create_project(conn, goal=f"p{i}") for i in range(3)]
    for p in other_projects:
        drift.record_lesson_retrieval(conn, note["id"], p["id"])
    conn.commit()

    archived = drift.decay_lessons(conn, k=3)
    assert note["id"] not in archived
    row = conn.execute("SELECT archived_at FROM notes WHERE id = ?", (note["id"],)).fetchone()
    assert row["archived_at"] is None


def test_lesson_not_retrieved_enough_is_archived(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(conn, task["id"], owner="a", lesson="stale lesson", trigger="t", do_instead="d", scope="s")
    note = conn.execute("SELECT id FROM notes WHERE kind = 'lesson'").fetchone()

    archived = drift.decay_lessons(conn, k=3)
    assert note["id"] in archived
    row = conn.execute("SELECT archived_at FROM notes WHERE id = ?", (note["id"],)).fetchone()
    assert row["archived_at"] is not None


def test_pinned_lesson_is_never_decayed(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(conn, task["id"], owner="a", lesson="pinned lesson", trigger="t", do_instead="d", scope="s")
    note = conn.execute("SELECT id FROM notes WHERE kind = 'lesson'").fetchone()
    conn.execute("UPDATE notes SET pinned = 1 WHERE id = ?", (note["id"],))
    conn.commit()

    archived = drift.decay_lessons(conn, k=3)
    assert note["id"] not in archived


def test_brief_node_records_lesson_retrieval(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(conn, task["id"], owner="a", lesson="l", trigger="t", do_instead="d", scope="s")
    note = conn.execute("SELECT id, last_retrieved_at FROM notes WHERE kind = 'lesson'").fetchone()
    assert note["last_retrieved_at"] is None

    queries.brief_node(conn, task["id"])

    row = conn.execute("SELECT last_retrieved_at FROM notes WHERE id = ?", (note["id"],)).fetchone()
    assert row["last_retrieved_at"] is not None
    retrieved_events = conn.execute(
        "SELECT * FROM events WHERE type = 'note.retrieved'"
    ).fetchall()
    assert len(retrieved_events) == 1


def test_archived_lesson_is_not_surfaced_in_brief(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    nodes.fail(conn, task["id"], owner="a", lesson="l", trigger="t", do_instead="d", scope="s")
    note = conn.execute("SELECT id FROM notes WHERE kind = 'lesson'").fetchone()
    conn.execute("UPDATE notes SET archived_at = 'now' WHERE id = ?", (note["id"],))
    conn.commit()

    brief = queries.brief_node(conn, task["id"])
    assert brief["lessons"] == []
