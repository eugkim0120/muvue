"""P3: `muvue adapter install` and `muvue hook <claude-event>` wired
through the real CLI (subprocess), not just the underlying core
functions -- proves the stdin/stdout/exit-code contract the Claude Code
adapter actually depends on."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from muvue.core import db as core_db, nodes, projects
from muvue.core.repo_init import init_repo


def _run(repo_root: Path, *args: str, input_text: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        cwd=repo_root, input=input_text, capture_output=True, text=True,
    )


def test_adapter_install_claude_code_via_cli(tmp_path: Path):
    init_repo(tmp_path)
    result = _run(tmp_path, "adapter", "install", "claude-code", "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "SessionStart" in settings["hooks"]


def test_hook_pre_tool_use_blocks_edit_with_no_node_and_exits_2(tmp_path: Path):
    init_repo(tmp_path)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    result = _run(tmp_path, "hook", "pre-tool-use", str(tmp_path), input_text=payload)
    assert result.returncode == 2
    assert "no active muvue node" in result.stderr


def test_hook_pre_tool_use_allows_edit_after_cli_start_sets_current_node(tmp_path: Path):
    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    project = projects.create_project(conn, goal="cli hook test")
    projects.set_phase(conn, project["id"], "executing")
    task = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.close()

    start_result = _run(tmp_path, "start", str(task["id"]), "--owner", "claude", "--path", str(tmp_path))
    assert start_result.returncode == 0, start_result.stderr
    assert (tmp_path / ".muvue" / "current_node").read_text().strip() == str(task["id"])

    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    hook_result = _run(tmp_path, "hook", "pre-tool-use", str(tmp_path), input_text=payload)
    assert hook_result.returncode == 0, hook_result.stderr
    assert hook_result.stdout == ""

    done_result = _run(
        tmp_path, "done", str(task["id"]), "--owner", "claude", "--summary", "ok", "--path", str(tmp_path)
    )
    assert done_result.returncode == 0, done_result.stderr
    assert not (tmp_path / ".muvue" / "current_node").exists()
