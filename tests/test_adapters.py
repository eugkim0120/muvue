"""P3: adapter config writers (plan section 7) -- Claude Code's real hook
config, Codex/Gemini/Cursor's best-effort instruction files, doctor's
protocol_version mismatch check, and the Claude Code hook decision logic
(core/claude_hooks.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muvue.core import adapters, claude_hooks, db as core_db, doctor, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


# -- config writers -----------------------------------------------------


def test_install_claude_code_writes_all_four_hooks(tmp_path: Path):
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    hooks = settings["hooks"]
    for event in ("SessionStart", "PreToolUse", "PreCompact", "Stop"):
        assert event in hooks
        commands = [h["command"] for g in hooks[event] for h in g["hooks"]]
        # v4 section 4a (P0.5): the Claude Code adapter's hook commands
        # invoke the stdlib-only fast path -- see src/muvue/_hook.py.
        assert any("-S -m muvue._hook" in c for c in commands)
    assert settings["_muvue"]["protocol_version"] == 1


def test_install_claude_code_preserves_unrelated_settings(tmp_path: Path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"otherSetting": True, "hooks": {}}))
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    assert settings["otherSetting"] is True


def test_install_claude_code_is_idempotent(tmp_path: Path):
    adapters.install_claude_code(tmp_path, protocol_version=1)
    path = adapters.install_claude_code(tmp_path, protocol_version=1)
    settings = json.loads(path.read_text())
    for event in ("SessionStart", "PreToolUse", "PreCompact", "Stop"):
        assert len(settings["hooks"][event]) == 1  # not duplicated


def test_install_claude_code_bumps_embedded_protocol_version(tmp_path: Path):
    adapters.install_claude_code(tmp_path, protocol_version=1)
    adapters.install_claude_code(tmp_path, protocol_version=2)
    assert adapters.claude_code_protocol_version(tmp_path) == 2


def test_install_codex_writes_agents_md(tmp_path: Path):
    path = adapters.install_codex(tmp_path, protocol_version=1)
    assert path.name == "AGENTS.md"
    assert "muvue" in path.read_text()
    assert "protocol_version=1" in path.read_text()


def test_install_codex_appends_to_existing_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Existing project notes\n")
    path = adapters.install_codex(tmp_path, protocol_version=1)
    content = path.read_text()
    assert "Existing project notes" in content
    assert "muvue" in content


def test_install_codex_is_idempotent_not_duplicated(tmp_path: Path):
    adapters.install_codex(tmp_path, protocol_version=1)
    adapters.install_codex(tmp_path, protocol_version=1)
    content = (tmp_path / "AGENTS.md").read_text()
    assert content.count("muvue task protocol") <= 1 or content.count("muvue mcp") == 1


def test_install_gemini_writes_gemini_md(tmp_path: Path):
    path = adapters.install_gemini(tmp_path, protocol_version=1)
    assert path.name == "GEMINI.md"


def test_install_cursor_writes_mdc_rule(tmp_path: Path):
    path = adapters.install_cursor(tmp_path, protocol_version=1)
    assert path.suffix == ".mdc"
    assert "protocol_version=1" in path.read_text()


# -- current-node pointer -------------------------------------------------


def test_current_node_round_trips(tmp_path: Path):
    assert adapters.get_current_node(tmp_path) is None
    adapters.set_current_node(tmp_path, 7)
    assert adapters.get_current_node(tmp_path) == 7
    adapters.clear_current_node(tmp_path)
    assert adapters.get_current_node(tmp_path) is None


# -- doctor: protocol_version mismatch -------------------------------------


def test_doctor_warns_on_adapter_protocol_version_mismatch(tmp_path: Path):
    init_repo(tmp_path)
    adapters.install_claude_code(tmp_path, protocol_version=999)  # stale on purpose
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("protocol_version" in issue for issue in report.issues)


def test_doctor_ok_when_adapter_protocol_version_matches(tmp_path: Path):
    init_repo(tmp_path)
    config = MuvueConfig()
    adapters.install_claude_code(tmp_path, protocol_version=config.protocol_version)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True


def test_doctor_ok_when_no_adapter_installed(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True


# -- claude_hooks decision logic -------------------------------------------


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="claude hooks test")


def test_pre_tool_use_blocks_edit_with_no_active_node(conn):
    result = claude_hooks.pre_tool_use(conn, tool_name="Edit", tool_input={}, node_id=None)
    assert result["decision"] == "block"


def test_pre_tool_use_allows_edit_with_in_progress_node(conn, project):
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="claude")
    result = claude_hooks.pre_tool_use(conn, tool_name="Edit", tool_input={}, node_id=task["id"])
    assert result["decision"] == "allow"


def test_pre_tool_use_blocks_edit_when_node_awaiting_approval(conn, project):
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="claude")
    conn.execute("UPDATE nodes SET status = 'awaiting_approval' WHERE id = ?", (task["id"],))
    conn.commit()
    result = claude_hooks.pre_tool_use(conn, tool_name="Edit", tool_input={}, node_id=task["id"])
    assert result["decision"] == "block"


def test_pre_tool_use_blocks_git_commit_without_trailer(conn):
    result = claude_hooks.pre_tool_use(
        conn, tool_name="Bash", tool_input={"command": "git commit -m 'fix bug'"}, node_id=None,
    )
    assert result["decision"] == "block"


def test_pre_tool_use_allows_git_commit_with_trailer(conn):
    result = claude_hooks.pre_tool_use(
        conn, tool_name="Bash",
        tool_input={"command": "git commit -m 'fix bug\n\nMuvue-Node: 5'"}, node_id=None,
    )
    assert result["decision"] == "allow"


def test_pre_tool_use_allows_unrelated_bash_commands(conn):
    result = claude_hooks.pre_tool_use(conn, tool_name="Bash", tool_input={"command": "ls -la"}, node_id=None)
    assert result["decision"] == "allow"


def test_session_start_returns_brief_as_additional_context(conn, project):
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    result = claude_hooks.session_start(conn, node_id=task["id"])
    assert result["decision"] == "allow"
    assert result["hookSpecificOutput"]["additionalContext"]["node"]["id"] == task["id"]


def test_session_start_allows_when_no_node(conn):
    assert claude_hooks.session_start(conn, node_id=None) == {"decision": "allow"}


def test_pre_compact_requires_summary_when_in_progress(conn, project):
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="claude")
    assert claude_hooks.pre_compact(conn, node_id=task["id"], summary=None)["decision"] == "block"
    assert claude_hooks.pre_compact(conn, node_id=task["id"], summary="progress so far")["decision"] == "allow"


def test_stop_blocks_in_progress_node_with_no_notes(conn, project):
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    nodes.start(conn, task["id"], owner="claude")
    assert claude_hooks.stop(conn, node_id=task["id"])["decision"] == "block"
    nodes.add_note(conn, task["id"], kind="discovery", text="progress", actor="agent")
    assert claude_hooks.stop(conn, node_id=task["id"])["decision"] == "allow"


def test_stop_allows_when_no_active_node(conn):
    assert claude_hooks.stop(conn, node_id=None)["decision"] == "allow"
