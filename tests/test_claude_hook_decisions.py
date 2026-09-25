"""Claude Code adapter hook decisions (plan section 7), all served by the
stdlib-only `muvue._hook` fast path:

- SessionStart -> inject the active node's brief as context.
- PreToolUse -> block Edit/Write with no `in_progress` node, or a node
  that's `awaiting_approval`.
- PreCompact -> require a progress summary before compacting.
- Stop -> block ending the turn with unlogged `in_progress` work.

Claude Code's contract (checked against the installed CLI): exit 2 blocks,
and stderr is fed back as the reason; on exit 0 SessionStart's stdout is
added as context. Stop must allow while `stop_hook_active` is true, or the
turn can loop forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue import _hook
from muvue.core import db as core_db
from muvue.core import nodes, projects
from muvue.core.repo_init import init_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="claude hooks test")
    return projects.set_phase(conn, p["id"], "executing")


@pytest.fixture
def started(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task",
                             title="Refactor pricing loader", status="ready",
                             criteria=["loader returns cents"])
    nodes.start(conn, task["id"], owner="claude")
    return nodes.get_node(conn, task["id"])


def run(repo, name, payload=None, **kw):
    return _hook.run(name, str(repo), payload or {}, **kw)


# -- PreToolUse ------------------------------------------------------------


def test_pre_tool_use_blocks_edit_with_no_active_node(repo, project):
    out = run(repo, "pre-tool-use", {"tool_name": "Edit"})
    assert out.exit_code == 2
    assert "no active muvue node" in out.stderr
    assert out.stdout == ""


def test_pre_tool_use_allows_edit_with_in_progress_node(repo, started):
    out = run(repo, "pre-tool-use", {"tool_name": "Edit", "node_id": started["id"]})
    assert (out.exit_code, out.stdout, out.stderr) == (0, "", "")


def test_pre_tool_use_blocks_edit_when_node_awaiting_approval(repo, conn, started):
    conn.execute("UPDATE nodes SET status = 'awaiting_approval' WHERE id = ?", (started["id"],))
    out = run(repo, "pre-tool-use", {"tool_name": "Write", "node_id": started["id"]})
    assert out.exit_code == 2
    assert "awaiting_approval" in out.stderr


def test_pre_tool_use_never_blocks_bash(repo, project):
    """v4 section 7 / changelog item 10: trailer enforcement is post-hoc
    (`post-commit`), not a string match on the Bash command."""
    out = run(repo, "pre-tool-use", {"tool_name": "Bash",
                                     "tool_input": {"command": "git commit -m x"}})
    assert out.exit_code == 0


def test_pre_tool_use_interrupts_a_query_past_the_deadline(repo, started, monkeypatch):
    """The 150 ms deadline is enforced *during* the query (sqlite progress
    handler), not just checked after it: a stuck query is interrupted,
    the call fails open and `hook_timeout` is spooled."""
    monkeypatch.setattr(_hook, "_PROGRESS_OPS", 1)
    ticks = iter([0.0] + [1.0] * 1000)
    out = run(repo, "pre-tool-use", {"tool_name": "Edit", "node_id": 999999},
              clock=lambda: next(ticks))
    assert out.exit_code == 0  # a missing node would block; timing out allows
    queue = (repo / ".muvue" / "queue.jsonl").read_text()
    assert '"hook_timeout"' in queue


# -- SessionStart ----------------------------------------------------------


def test_session_start_injects_the_active_node_brief(repo, started):
    out = run(repo, "session-start", {"node_id": started["id"]})
    assert out.exit_code == 0
    assert f"T{started['id']}" in out.stdout
    assert "Refactor pricing loader" in out.stdout
    assert "loader returns cents" in out.stdout
    assert f"muvue brief {started['id']}" in out.stdout


def test_session_start_is_silent_with_no_active_node(repo, project):
    out = run(repo, "session-start", {})
    assert (out.exit_code, out.stdout) == (0, "")


def test_session_start_uses_the_current_node_file(repo, started):
    (repo / ".muvue" / "current_node").write_text(str(started["id"]))
    assert "Refactor pricing loader" in run(repo, "session-start", {}).stdout


# -- Stop -------------------------------------------------------------------


def test_stop_blocks_in_progress_node_with_no_note_since_start(repo, conn, started):
    out = run(repo, "stop", {"node_id": started["id"]})
    assert out.exit_code == 2
    assert "muvue note" in out.stderr
    nodes.add_note(conn, started["id"], kind="discovery", text="progress", actor="agent")
    assert run(repo, "stop", {"node_id": started["id"]}).exit_code == 0


def test_stop_counts_only_notes_logged_since_the_latest_start(repo, conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="claude")
    nodes.add_note(conn, task["id"], kind="discovery", text="first run", actor="agent")
    nodes.fail(conn, task["id"], owner="claude", lesson="x", trigger="t", do_instead="d", scope="s")
    nodes.start(conn, task["id"], owner="claude")
    assert run(repo, "stop", {"node_id": task["id"]}).exit_code == 2


def test_stop_allows_while_stop_hook_active_to_avoid_a_loop(repo, started):
    out = run(repo, "stop", {"node_id": started["id"], "stop_hook_active": True})
    assert out.exit_code == 0


def test_stop_allows_when_no_active_node(repo, project):
    assert run(repo, "stop", {}).exit_code == 0


# -- PreCompact -------------------------------------------------------------


def test_pre_compact_requires_a_progress_note_when_in_progress(repo, conn, started):
    out = run(repo, "pre-compact", {"node_id": started["id"], "trigger": "auto"})
    assert out.exit_code == 2
    assert "before compacting" in out.stderr
    nodes.add_note(conn, started["id"], kind="discovery", text="state so far", actor="agent")
    assert run(repo, "pre-compact", {"node_id": started["id"]}).exit_code == 0


def test_pre_compact_accepts_a_summary_in_the_payload(repo, started):
    out = run(repo, "pre-compact", {"node_id": started["id"], "summary": "progress so far"})
    assert out.exit_code == 0


# -- every event is still spooled ---------------------------------------------


@pytest.mark.parametrize("name", ["session-start", "stop", "pre-compact"])
def test_decision_hooks_still_spool_their_event(repo, started, name):
    run(repo, name, {"node_id": started["id"], "stop_hook_active": True, "summary": "s"})
    assert f'"event": "{name}"' in (repo / ".muvue" / "queue.jsonl").read_text()


def test_decision_hooks_fail_open_without_a_db(repo):
    (repo / ".muvue" / "muvue.db").unlink()
    for name in ("session-start", "stop", "pre-compact"):
        assert run(repo, name, {"node_id": 1}).exit_code == 0


def test_cli_hook_command_uses_the_same_decisions(repo, started):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "stop", str(repo)],
        input=f'{{"node_id": {started["id"]}}}', capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "muvue note" in proc.stderr


# -- Stop: reconcile-on-touch (v4 section 9, drift loop 3) ------------------


def _stale_component(conn, path: str) -> int:
    import json

    with core_db.write_txn(conn):
        cur = conn.execute(
            "INSERT INTO components (name, anchors_json, status) VALUES (?, ?, 'stale')",
            ("pricing", json.dumps({path: "oldhash"})),
        )
    return cur.lastrowid


def test_stop_blocks_while_a_touched_component_is_stale_and_unreconciled(repo, conn, project):
    from muvue.core import hooks

    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t",
                             status="ready", predicted_touches=["src/other.py"])
    nodes.start(conn, task["id"], owner="claude")
    cid = _stale_component(conn, "pricing/loader.py")
    # Only the actual commit touches the component (predicted doesn't).
    hooks.handle_post_commit(conn, commit_sha="abc", message=f"x\n\nMuvue-Node: {task['id']}",
                             files=["pricing/loader.py"])
    nodes.add_note(conn, task["id"], kind="discovery", text="did some work", actor="agent")
    out = run(repo, "stop", {"node_id": task["id"]})
    assert out.exit_code == 2
    assert f"C{cid}" in out.stderr and "stale" in out.stderr

    nodes.add_note(conn, task["id"], kind="discovery", actor="agent",
                   text=f"C{cid}: loader now reads v2 files; purpose unchanged")
    assert run(repo, "stop", {"node_id": task["id"]}).exit_code == 0


def test_stop_ignores_stale_components_the_node_does_not_touch(repo, conn, started):
    _stale_component(conn, "elsewhere.py")
    nodes.add_note(conn, started["id"], kind="discovery", text="progress", actor="agent")
    assert run(repo, "stop", {"node_id": started["id"]}).exit_code == 0
