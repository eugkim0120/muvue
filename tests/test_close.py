"""P6: `close` proposes then commits a project's structure diff
(new components/decisions/promoted lessons), writes
.muvue/components.json / .muvue/decisions.json and commits them on
`main`, exports the project's event history to
.muvue/history/<project-id>.jsonl.gz, and sets phase -> closed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from muvue.core import close as close_mod
from muvue.core import db as core_db
from muvue.core import nodes, projects


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
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
    (root / "README.md").write_text("hi\n")
    (root / ".muvue").mkdir()
    # `.muvue/muvue.db` is gitignored (plan section 2 file layout table);
    # a real `init` commits this alongside `config.toml`. Tracking it here,
    # in the *initial* commit, keeps the fixture's tree clean by default --
    # otherwise the untracked `.muvue/` dir itself would make every close
    # test's working tree look dirty to `git status --porcelain`, which is
    # exactly the signal v4 section 9's fast-forward gate reads.
    (root / ".muvue" / ".gitignore").write_text("muvue.db\nmuvue.db-*\nqueue.jsonl\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


@pytest.fixture
def conn(repo: Path):
    c = core_db.init_db(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="close test")
    return projects.set_phase(conn, p["id"], "executing")


def _done_task(conn, project, **kw):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", status="ready", **kw,
    )
    nodes.start(conn, task["id"], owner="a1")
    return nodes.done(conn, task["id"], owner="a1")["node"]


def test_preview_close_refuses_when_nodes_not_done(conn, project):
    nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    preview = close_mod.preview_close(conn, project["id"])
    assert preview["closeable"] is False
    assert len(preview["blocking_nodes"]) == 1


def test_preview_close_surfaces_decisions_and_lessons(conn, project):
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")
    nodes.add_note(conn, task_row["id"], kind="lesson",
                    text=json.dumps({"trigger": "flaky ci", "failure": "test flakes",
                                      "do_instead": "retry with backoff", "scope": "t1"}),
                    pinned=True)

    preview = close_mod.preview_close(conn, project["id"])
    assert preview["closeable"] is True
    assert len(preview["diff"]["decisions"]) == 1
    assert "sqlite WAL" in preview["diff"]["decisions"][0]["choice"]
    assert len(preview["diff"]["promoted_lessons"]) == 1
    assert preview["diff"]["promoted_lessons"][0]["choice"] == "retry with backoff"


def test_close_project_dry_run_does_not_mutate(conn, project, repo):
    _done_task(conn, project, title="t1")
    result = close_mod.close_project(conn, project["id"], repo, confirm=False)
    assert result["confirmed"] is False
    assert projects.get_project(conn, project["id"])["phase"] == "executing"
    assert not (repo / ".muvue" / "components.json").exists()


def test_close_project_refuses_non_human(conn, project, repo):
    _done_task(conn, project, title="t1")
    with pytest.raises(close_mod.HumanOnly):
        close_mod.close_project(conn, project["id"], repo, confirm=True, actor="agent")


def test_close_project_confirmed_writes_and_commits(conn, project, repo):
    """P6-era test, kept but updated for v4 section 9: the commit this
    fixture ends up seeing on `main` is no longer `close_project` committing
    straight onto the checked-out branch -- it's a `muvue/structure` commit
    that gets fast-forwarded onto `main` because this fixture's repo happens
    to be on `main` with a clean tree (the v4-safe case). See
    `test_close_project_clean_main_fast_forwards` for the explicit
    ref-level assertions, and `test_close_project_non_main_branch_leaves_inbox_item`
    / `test_close_project_dirty_main_leaves_inbox_item_and_working_tree_untouched`
    for the two cases where v3's "commit directly on main" would have
    corrupted the user's checkout."""
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")

    result = close_mod.close_project(conn, project["id"], repo, confirm=True)

    assert result["confirmed"] is True
    assert result["project"]["phase"] == "closed"
    assert projects.get_project(conn, project["id"])["phase"] == "closed"
    assert result["fast_forwarded"] is True
    assert result["structure_ref"] == close_mod.STRUCTURE_REF

    comp_path = Path(result["components_path"])
    dec_path = Path(result["decisions_path"])
    assert comp_path.exists()
    assert dec_path.exists()
    decisions_on_disk = json.loads(dec_path.read_text())
    assert any("sqlite WAL" in (d.get("choice") or "") for d in decisions_on_disk)

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "close" in log.lower()

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".muvue/components.json", ".muvue/decisions.json"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert status.strip() == ""  # committed, nothing left dirty

    history_path = Path(result["history_path"])
    assert history_path.exists()
    assert history_path.name == f"{project['id']}.jsonl.gz"


def test_close_project_caps_diff_size(conn, project, repo, monkeypatch):
    monkeypatch.setattr(close_mod, "MAX_DIFF_ITEMS", 2)
    task_row = _done_task(conn, project, title="t1")
    for i in range(5):
        nodes.add_note(conn, task_row["id"], kind="decision", text=f"decision number {i}")
    preview = close_mod.preview_close(conn, project["id"])
    assert len(preview["diff"]["decisions"]) == 2


# -- v4 section 9: structure commits never touch a checked-out branch
# directly -- they land on `muvue/structure` via a temporary index, then
# fast-forward `main` only when it's checked out *and* clean, else leave
# an inbox item. ---------------------------------------------------------

def _porcelain(repo: Path) -> str:
    """`git status --porcelain`, filtered to exclude `.muvue/history/` --
    `close_project`'s event-history export (`core/history.py`, P6, out of
    this session's scope) writes there unconditionally and is untracked
    by this fixture's `.gitignore`, which would otherwise make every
    "working tree untouched" assertion below fail on a change this
    session didn't make and isn't testing."""
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    return "\n".join(
        line for line in out.splitlines() if ".muvue/history" not in line
    )


def _log(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "log", *args], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def _rev_parse(repo: Path, rev: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", rev], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _ref_exists(repo: Path, ref: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", ref], cwd=repo, capture_output=True, text=True,
    ).returncode == 0


def test_structure_commit_never_touches_real_index_or_working_tree(conn, project, repo):
    """Direct unit test of the git plumbing (`close_mod._write_structure_commit`):
    a real uncommitted change (staged AND unstaged) sits in the working
    tree throughout, and must be byte-identical before/after."""
    (repo / "README.md").write_text("hi\nunstaged change\n")
    (repo / "staged.txt").write_text("staged content\n")
    _git(repo, "add", "staged.txt")

    before_status = _porcelain(repo)
    before_readme = (repo / "README.md").read_text()
    assert before_status != ""  # sanity: repo really is dirty

    sha = close_mod._write_structure_commit(
        repo, {".muvue/components.json": "[]\n", ".muvue/decisions.json": "[]\n"},
        "muvue: test structure commit",
    )

    after_status = _porcelain(repo)
    after_readme = (repo / "README.md").read_text()
    assert after_status == before_status
    assert after_readme == before_readme
    assert not (repo / ".muvue" / "components.json").exists()
    assert not (repo / "staged.txt.orig").exists()

    assert _ref_exists(repo, close_mod.STRUCTURE_REF)
    assert _rev_parse(repo, close_mod.STRUCTURE_REF) == sha
    # main itself must be completely untouched
    assert _rev_parse(repo, "main") != sha


def test_close_project_clean_main_fast_forwards(conn, project, repo):
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")

    assert _porcelain(repo) == ""  # sanity: clean tree, on main
    result = close_mod.close_project(conn, project["id"], repo, confirm=True)

    assert result["confirmed"] is True
    assert result["fast_forwarded"] is True
    assert result["inbox_event_id"] is None

    comp_path = Path(result["components_path"])
    dec_path = Path(result["decisions_path"])
    assert comp_path.exists()
    assert dec_path.exists()
    decisions_on_disk = json.loads(dec_path.read_text())
    assert any("sqlite WAL" in (d.get("choice") or "") for d in decisions_on_disk)

    # main's real HEAD now includes the structure commit
    assert _rev_parse(repo, "main") == result["structure_sha"]
    assert _rev_parse(repo, close_mod.STRUCTURE_REF) == result["structure_sha"]
    log = _log(repo, "--oneline", "-1")
    assert "close" in log.lower()
    assert _porcelain(repo) == ""  # still clean after the fast-forward


def test_close_project_non_main_branch_leaves_inbox_item(conn, project, repo):
    _git(repo, "checkout", "-q", "-b", "feature/other")
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")

    main_before = _rev_parse(repo, "main")
    result = close_mod.close_project(conn, project["id"], repo, confirm=True)

    assert result["confirmed"] is True
    assert result["fast_forwarded"] is False
    assert result["inbox_event_id"] is not None

    assert _rev_parse(repo, "main") == main_before  # main untouched
    assert _ref_exists(repo, close_mod.STRUCTURE_REF)
    assert _rev_parse(repo, close_mod.STRUCTURE_REF) == result["structure_sha"]
    assert not (repo / ".muvue" / "components.json").exists()

    event = conn.execute(
        "SELECT * FROM events WHERE type = 'inbox.structure_update_ready' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event is not None
    payload = json.loads(event["payload"])
    assert payload["ref"] == close_mod.STRUCTURE_REF
    assert payload["sha"] == result["structure_sha"]


def test_close_project_dirty_main_leaves_inbox_item_and_working_tree_untouched(conn, project, repo):
    (repo / "README.md").write_text("hi\nuncommitted local edit\n")
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode for concurrency")

    before_status = _porcelain(repo)
    before_readme = (repo / "README.md").read_text()
    main_before = _rev_parse(repo, "main")

    result = close_mod.close_project(conn, project["id"], repo, confirm=True)

    assert result["confirmed"] is True
    assert result["fast_forwarded"] is False
    assert result["inbox_event_id"] is not None

    after_status = _porcelain(repo)
    after_readme = (repo / "README.md").read_text()
    assert after_status == before_status  # the dirty README edit survives, untouched
    assert after_readme == before_readme
    assert _rev_parse(repo, "main") == main_before  # main's HEAD never moved

    assert _ref_exists(repo, close_mod.STRUCTURE_REF)
    assert _rev_parse(repo, close_mod.STRUCTURE_REF) == result["structure_sha"]


def test_close_with_pr_opens_one_when_main_cannot_fast_forward(conn, project, repo):
    _git(repo, "checkout", "-q", "-b", "feature/other")
    task_row = _done_task(conn, project, title="t1")
    nodes.add_note(conn, task_row["id"], kind="decision", text="use sqlite WAL mode")
    calls = []

    def fake_opener(repo_root, sha, project_id):
        calls.append((repo_root, sha, project_id))
        return "https://github.com/o/r/pull/7"

    result = close_mod.close_project(conn, project["id"], repo, confirm=True, open_pr=True,
                                     pr_opener=fake_opener)
    assert calls == [(repo, result["structure_sha"], project["id"])]
    assert result["pr_url"] == "https://github.com/o/r/pull/7"
    payload = json.loads(conn.execute(
        "SELECT payload FROM events WHERE type = 'inbox.structure_update_ready'").fetchone()[0])
    assert payload["pr_url"] == "https://github.com/o/r/pull/7"


def test_close_pr_failure_is_reported_not_raised(conn, project, repo):
    _git(repo, "checkout", "-q", "-b", "feature/other")
    _done_task(conn, project, title="t1")
    # The fixture repo has no GitHub origin: the real opener refuses.
    result = close_mod.close_project(conn, project["id"], repo, confirm=True, open_pr=True)
    assert result["pr_url"] is None
    assert "not a GitHub remote" in result["pr_error"]
    assert result["inbox_event_id"] is not None


def test_github_slug_parses_ssh_and_https_origins(repo):
    _git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    assert close_mod._github_slug(repo) == "acme/widgets"
    _git(repo, "remote", "set-url", "origin", "https://github.com/acme/widgets")
    assert close_mod._github_slug(repo) == "acme/widgets"
    _git(repo, "remote", "set-url", "origin", "https://gitlab.com/acme/widgets.git")
    assert close_mod._github_slug(repo) is None
