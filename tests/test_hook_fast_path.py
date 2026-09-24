"""v4 section 4a: `muvue._hook`, the stdlib-only hook fast path.

Unit-level tests exercise the module's functions directly (in-process,
no subprocess) -- the cold-process latency claim itself is a separate
benchmark (tests/test_hook_latency_benchmark.py), and the "never
imports a third-party module" claim is a separate import-graph proof
(tests/test_hook_import_graph.py). This file is about the fast path's
*behavior*: what gets spooled, and the PreToolUse allow/deny/fail-open
logic.
"""

from __future__ import annotations

import io
import json
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


def _queue_lines(repo_root: Path) -> list[dict]:
    path = repo_root / ".muvue" / "queue.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- default action: append to queue and exit --------------------------


def test_post_commit_default_action_spools_sha_and_exits_zero(repo, monkeypatch):
    git_dir = repo / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "refs" / "heads" / "main").write_text("deadbeef" * 5 + "\n")

    exit_code = _hook.main(["post-commit", str(repo)])

    assert exit_code == 0
    lines = _queue_lines(repo)
    assert len(lines) == 1
    assert lines[0]["event"] == "post-commit"
    assert lines[0]["sha"] == "deadbeef" * 5
    assert "ts" in lines[0]


def test_post_commit_resolves_packed_refs_fallback(repo):
    git_dir = repo / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "packed-refs").write_text("# pack-refs\n" + "cafebabe" * 5 + " refs/heads/main\n")

    _hook.main(["post-commit", str(repo)])

    lines = _queue_lines(repo)
    assert lines[0]["sha"] == "cafebabe" * 5


def test_session_start_default_action_spools_and_allows(repo, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"node_id": 7})))
    import sys

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    exit_code = _hook.main(["session-start", str(repo)])

    assert exit_code == 0
    assert json.loads(out.getvalue()) == {"decision": "allow"}
    lines = _queue_lines(repo)
    assert lines == [{"event": "session-start", "ts": lines[0]["ts"], "node_id": 7}]


def test_stop_default_action_never_reads_db_no_db_present(repo, monkeypatch):
    # No .muvue/muvue.db in this repo at all (init_repo does create one,
    # so delete it) -- proves `stop` truly never opens the DB, unlike
    # the old synchronous core.claude_hooks.stop which queried `notes`.
    (repo / ".muvue" / "muvue.db").unlink()
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    exit_code = _hook.main(["stop", str(repo)])
    assert exit_code == 0
    assert _queue_lines(repo)[0]["event"] == "stop"


def test_pre_push_default_action_spools_minimal_event(repo):
    exit_code = _hook.main(["pre-push", str(repo)])
    assert exit_code == 0
    assert _queue_lines(repo)[0]["event"] == "pre-push"


def test_no_muvue_dir_fails_open_silently(tmp_path):
    exit_code = _hook.main(["post-commit", str(tmp_path)])
    assert exit_code == 0
    assert not (tmp_path / ".muvue").exists()


def test_unknown_hook_name_fails_open(repo):
    assert _hook.main(["pre-receive", str(repo)]) == 0
    assert _queue_lines(repo) == []


# -- PreToolUse: the one DB-reading path --------------------------------


@pytest.fixture
def conn_and_project(repo):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    proj = projects.create_project(conn, goal="hook fast path test")
    projects.set_phase(conn, proj["id"], "executing")
    conn.close()
    return proj


def test_pre_tool_use_allows_non_blocking_tools_without_opening_db(repo, monkeypatch):
    # Point at a DB path that doesn't exist to prove this really never
    # touches sqlite3 for a non-blocking tool.
    (repo / ".muvue" / "muvue.db").unlink()
    result = _hook.pre_tool_use(str(repo), {"tool_name": "Read", "tool_input": {}})
    assert result == {"decision": "allow"}


def test_pre_tool_use_blocks_edit_with_no_current_node(repo, conn_and_project):
    result = _hook.pre_tool_use(str(repo), {"tool_name": "Edit"})
    assert result["decision"] == "block"
    assert "no active muvue node" in result["reason"]


def test_pre_tool_use_allows_edit_on_in_progress_node(repo, conn_and_project):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    node = nodes.create_node(
        conn, project_id=conn_and_project["id"], kind="task", title="t", status="ready"
    )
    nodes.start(conn, node_id=node["id"], owner="agent-1")
    conn.close()

    result = _hook.pre_tool_use(str(repo), {"tool_name": "Edit", "node_id": node["id"]})
    assert result == {"decision": "allow"}


def test_pre_tool_use_blocks_edit_on_awaiting_approval_node(repo, conn_and_project):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    node = nodes.create_node(
        conn,
        project_id=conn_and_project["id"],
        kind="task",
        title="t",
        status="awaiting_approval",
    )
    conn.close()
    result = _hook.pre_tool_use(str(repo), {"tool_name": "Edit", "node_id": node["id"]})
    assert result["decision"] == "block"
    assert "awaiting_approval" in result["reason"]


def test_pre_tool_use_reads_current_node_file_when_payload_has_none(repo, conn_and_project):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    node = nodes.create_node(
        conn, project_id=conn_and_project["id"], kind="task", title="t", status="ready"
    )
    nodes.start(conn, node_id=node["id"], owner="agent-1")
    conn.close()
    (repo / ".muvue" / "current_node").write_text(str(node["id"]))

    result = _hook.pre_tool_use(str(repo), {"tool_name": "Write"})
    assert result == {"decision": "allow"}


def test_pre_tool_use_bash_no_longer_blocks_git_commit_without_trailer(repo, conn_and_project):
    """v4 section 7 / changelog item 10: deliberate behavior *reduction*
    (mirrors core.claude_hooks -- see test_adapters.py's equivalent
    test). Trailer enforcement moved to post-hoc `post-commit`
    detection; see tests/test_trailer_enforcement_relocation.py."""
    result = _hook.pre_tool_use(
        str(repo), {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}
    )
    assert result == {"decision": "allow"}


def test_pre_tool_use_bash_allows_git_commit_with_trailer(repo, conn_and_project):
    result = _hook.pre_tool_use(
        str(repo),
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x' -m 'Muvue-Node: 1'"}},
    )
    assert result == {"decision": "allow"}


def test_pre_tool_use_bash_allows_non_commit_commands(repo, conn_and_project):
    result = _hook.pre_tool_use(str(repo), {"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert result == {"decision": "allow"}


def test_pre_tool_use_fails_open_and_spools_hook_timeout_on_deadline_overrun(
    repo, conn_and_project
):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    node = nodes.create_node(
        conn, project_id=conn_and_project["id"], kind="task", title="t", status="ready"
    )
    conn.close()

    # Deterministic overrun: clock() returns 0 on entry, then a value
    # past the deadline on the post-query check -- no real sleeping.
    calls = iter([0.0, 1.0])
    result = _hook.pre_tool_use(
        str(repo),
        {"tool_name": "Edit", "node_id": node["id"]},
        clock=lambda: next(calls),
        deadline_s=0.150,
    )
    assert result == {"decision": "allow"}
    lines = _queue_lines(repo)
    assert lines == [
        {"event": "hook_timeout", "ts": lines[0]["ts"], "hook": "pre-tool-use", "node_id": node["id"]}
    ]


def test_pre_tool_use_within_deadline_does_not_spool(repo, conn_and_project):
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    node = nodes.create_node(
        conn, project_id=conn_and_project["id"], kind="task", title="t", status="ready"
    )
    nodes.start(conn, node_id=node["id"], owner="agent-1")
    conn.close()

    calls = iter([0.0, 0.01])
    result = _hook.pre_tool_use(
        str(repo), {"tool_name": "Edit", "node_id": node["id"]}, clock=lambda: next(calls)
    )
    assert result == {"decision": "allow"}
    assert _queue_lines(repo) == []
