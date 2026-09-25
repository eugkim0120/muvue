"""v4 section 3 data-model deltas closed by the delta audit (W3):

- `deps` has a real writer (`create_node(depends_on=...)`, CLI
  `decompose/replan --depends-on`) and replays;
- `project_links` has a writer (`project create --follows/--supersedes`);
- the v3 per-project budget columns are gone (v4: budgets are per driver);
- lessons carry `trigger`, `failure`, `do_instead`, `scope`;
- replay covers deps, commits, approvals and spend, and `rebuild --apply`
  reconstructs the replayable set.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import gates, hooks, migrate, nodes, projects, rebuild, revisions, spend
from muvue.core.repo_init import init_repo
from muvue.core.schema import SCHEMA_VERSION


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="data model")
    return projects.set_phase(conn, p["id"], "executing")


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "muvue", *args, "--path", str(repo)],
                          cwd=repo, capture_output=True, text=True)


# -- deps ----------------------------------------------------------------------


def test_create_node_writes_deps_and_a_replayable_event(conn, project):
    a = nodes.create_node(conn, project_id=project["id"], kind="task", title="a")
    b = nodes.create_node(conn, project_id=project["id"], kind="task", title="b",
                          depends_on=[a["id"]])
    rows = conn.execute("SELECT node_id, depends_on FROM deps").fetchall()
    assert [tuple(r) for r in rows] == [(b["id"], a["id"])]
    assert conn.execute("SELECT COUNT(*) c FROM events WHERE type = 'dep.added'").fetchone()["c"] == 1
    assert rebuild.diff_state(conn) == {}


def test_depends_on_must_be_a_live_node_in_the_same_project(conn, project):
    other = projects.create_project(conn, goal="other")
    foreign = nodes.create_node(conn, project_id=other["id"], kind="task", title="x")
    with pytest.raises(nodes.NodeError, match="same project"):
        nodes.create_node(conn, project_id=project["id"], kind="task", title="b",
                          depends_on=[foreign["id"]])
    with pytest.raises(LookupError):
        nodes.create_node(conn, project_id=project["id"], kind="task", title="b",
                          depends_on=[999])


def test_cli_decompose_accepts_depends_on(tmp_path: Path):
    init_repo(tmp_path)
    pid = json.loads(_cli(tmp_path, "project", "create", "--goal", "g").stdout)["id"]
    spec = json.loads(_cli(tmp_path, "spec", str(pid), "--title", "s", "--body", "b").stdout)
    _cli(tmp_path, "approve", f"spec:{spec['id']}")
    first = json.loads(_cli(tmp_path, "decompose", str(spec["id"]), "--title", "one").stdout)
    second = _cli(tmp_path, "decompose", str(spec["id"]), "--title", "two",
                  "--depends-on", str(first["id"]))
    assert second.returncode == 0, second.stderr
    c = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        deps = [tuple(r) for r in c.execute("SELECT node_id, depends_on FROM deps")]
    finally:
        c.close()
    assert deps == [(json.loads(second.stdout)["id"], first["id"])]


# -- project_links -------------------------------------------------------------


def test_project_create_records_follows_and_supersedes_links(conn):
    old = projects.create_project(conn, goal="v1")
    prev = projects.create_project(conn, goal="v2")
    new = projects.create_project(conn, goal="v3", follows=[prev["id"]], supersedes=[old["id"]])
    links = {tuple(r) for r in conn.execute("SELECT src, dst, kind FROM project_links")}
    assert links == {(new["id"], prev["id"], "follows"), (new["id"], old["id"], "supersedes")}


def test_project_link_target_must_exist(conn):
    with pytest.raises(LookupError):
        projects.create_project(conn, goal="x", follows=[42])


def test_cli_project_create_follows(tmp_path: Path):
    init_repo(tmp_path)
    first = json.loads(_cli(tmp_path, "project", "create", "--goal", "a").stdout)["id"]
    out = _cli(tmp_path, "project", "create", "--goal", "b", "--follows", str(first))
    assert out.returncode == 0, out.stderr


# -- legacy budget columns -------------------------------------------------------


def test_projects_table_has_no_v3_budget_columns(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    assert not cols & {"budget_unit", "budget_limit", "spent"}


def test_migrate_drops_the_v3_budget_columns_and_keeps_rows(tmp_path: Path):
    db_path = tmp_path / ".muvue" / "muvue.db"
    db_path.parent.mkdir(parents=True)
    c = core_db.init_db(db_path)
    c.execute("ALTER TABLE projects ADD COLUMN budget_unit TEXT NOT NULL DEFAULT 'usd'")
    c.execute("ALTER TABLE projects ADD COLUMN budget_limit REAL NOT NULL DEFAULT 0")
    c.execute("ALTER TABLE projects ADD COLUMN spent REAL NOT NULL DEFAULT 0")
    c.execute("INSERT INTO projects (goal) VALUES ('kept')")
    c.execute("UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'")
    c.commit()
    c.close()
    assert migrate.run_migrate(tmp_path) == SCHEMA_VERSION
    c = core_db.connect(db_path)
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(projects)")}
        goals = [r["goal"] for r in c.execute("SELECT goal FROM projects")]
    finally:
        c.close()
    assert not cols & {"budget_unit", "budget_limit", "spent"}
    assert goals == ["kept"]


def test_cli_project_create_has_no_budget_flags(tmp_path: Path):
    init_repo(tmp_path)
    out = _cli(tmp_path, "project", "create", "--goal", "g", "--budget-unit", "usd")
    assert out.returncode != 0
    from conftest import plain

    assert "--budget-unit" in plain(out.stderr + out.stdout)


# -- structured lessons ----------------------------------------------------------


def test_fail_requires_all_four_lesson_fields(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="a")
    with pytest.raises(ValueError, match="do_instead"):
        nodes.fail(conn, task["id"], owner="a", lesson="broke", trigger="ci", scope="t")
    assert nodes.get_node(conn, task["id"])["status"] == "in_progress"


def test_lesson_note_must_be_structured(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t")
    with pytest.raises(ValueError, match="trigger"):
        nodes.add_note(conn, task["id"], kind="lesson", text="free text", actor="agent")
    note = nodes.add_note(conn, task["id"], kind="lesson", actor="agent",
                          text=nodes.lesson_text(trigger="ci red", failure="flaky",
                                                 do_instead="pin seed", scope="tests/"))
    assert json.loads(note["note"]["text"])["scope"] == "tests/"


def test_cli_fail_takes_the_lesson_fields(tmp_path: Path):
    init_repo(tmp_path)
    c = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    p = projects.set_phase(c, projects.create_project(c, goal="g")["id"], "executing")
    t = nodes.create_node(c, project_id=p["id"], kind="task", title="t", status="ready")
    nodes.start(c, t["id"], owner="a")
    c.close()
    out = _cli(tmp_path, "fail", str(t["id"]), "--owner", "a", "--lesson", "broke",
               "--trigger", "ci", "--do-instead", "retry", "--scope", "src/")
    assert out.returncode == 0, out.stderr
    missing = _cli(tmp_path, "fail", str(t["id"]), "--owner", "a", "--lesson", "broke")
    assert missing.returncode != 0


# -- replay coverage -------------------------------------------------------------


def test_replay_detects_tampered_deps(conn, project):
    a = nodes.create_node(conn, project_id=project["id"], kind="task", title="a")
    nodes.create_node(conn, project_id=project["id"], kind="task", title="b", depends_on=[a["id"]])
    conn.execute("DELETE FROM deps")
    assert "deps" in rebuild.diff_state(conn)


def test_replay_covers_commits_and_actual_touches(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t")
    hooks.handle_post_commit(conn, commit_sha="abc123", message=f"x\n\nMuvue-Node: {task['id']}",
                             files=["src/a.py"])
    assert rebuild.diff_state(conn) == {}
    conn.execute("DELETE FROM actual_touches")
    assert "actual_touches" in rebuild.diff_state(conn)


def test_replay_covers_spend(conn, project):
    spend.record_spend(conn, project["id"], "claude", "requests", 2)
    spend.record_spend(conn, project["id"], "claude", "requests", 3)
    assert rebuild.diff_state(conn) == {}
    conn.execute("UPDATE agent_spend SET spent = 1")
    assert "agent_spend" in rebuild.diff_state(conn)


def test_replay_covers_plan_revision_approvals(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t",
                             criteria=["c"], criteria_mode="auto")
    revisions.propose_revision(conn, project["id"], [task["id"]])
    revisions.approve_revision(conn, project["id"], 1)
    assert rebuild.diff_state(conn) == {}
    conn.execute("UPDATE plan_revisions SET approved_at = NULL")
    assert "plan_revisions" in rebuild.diff_state(conn)


# -- rebuild --apply ---------------------------------------------------------------


def test_apply_rebuild_restores_the_replayable_set_and_keeps_other_tables(conn, project):
    from muvue.core import asks

    a = nodes.create_node(conn, project_id=project["id"], kind="task", title="a", status="ready")
    nodes.create_node(conn, project_id=project["id"], kind="task", title="b", depends_on=[a["id"]])
    nodes.start(conn, a["id"], owner="x")
    asks.ask(conn, a["id"], question="which db?", default="sqlite")
    spend.record_spend(conn, project["id"], "claude", "requests", 4)
    conn.execute("UPDATE nodes SET status = 'done', owner = NULL WHERE id = ?", (a["id"],))
    conn.execute("DELETE FROM deps")
    conn.execute("DELETE FROM agent_spend")
    assert rebuild.diff_state(conn) != {}

    result = rebuild.apply_rebuild(conn)
    assert rebuild.diff_state(conn) == {}
    assert result["tables"]
    assert nodes.get_node(conn, a["id"])["status"] == "in_progress"
    assert conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"] == 1


def test_cli_rebuild_apply_keeps_a_backup(tmp_path: Path):
    init_repo(tmp_path)
    c = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    p = projects.create_project(c, goal="g")
    c.execute("UPDATE projects SET goal = 'tampered' WHERE id = ?", (p["id"],))
    c.close()
    out = subprocess.run([sys.executable, "-m", "muvue", "rebuild", str(tmp_path), "--apply"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    backups = list((tmp_path / ".muvue").glob("muvue.db.bak-*"))
    assert len(backups) == 1
    c = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        assert c.execute("SELECT goal FROM projects").fetchone()["goal"] == "g"
    finally:
        c.close()
