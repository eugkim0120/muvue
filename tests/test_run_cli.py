"""CLI-level smoke tests for P5's `muvue run`, `muvue merge`, `muvue
handoff` -- proves the CLI is a thin wrapper over `core.runner`/
`core.merge`/`core.nodes.handoff` (plan working rule 3), driven entirely
through subprocess `muvue` invocations, the same way
`tests/test_planning_cli.py` proved the planning verbs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core.repo_init import init_repo

from conftest import use_passing_checks

FAKE_AGENT_AVAILABLE = shutil.which("muvue-fake-agent") is not None
pytestmark = pytest.mark.skipif(
    not FAKE_AGENT_AVAILABLE, reason="muvue-fake-agent console script not installed"
)


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        cwd=repo_root, capture_output=True, text=True,
    )


def _setup_ready_task(tmp_path: Path) -> Path:
    """`init_repo`'s default config.toml already wires `[agents.fake]` +
    `[routing] * = "fake"` (see core/config.py's DEFAULT_CONFIG_TOML) --
    exactly plan section 2's documented default."""
    init_repo(tmp_path)
    use_passing_checks(tmp_path)
    project = json.loads(_run(tmp_path, "project", "create", "--goal", "g", "--path", str(tmp_path)).stdout)
    spec = json.loads(
        _run(
            tmp_path, "spec", str(project["id"]), "--title", "s", "--body", "b", "--path", str(tmp_path),
        ).stdout
    )
    _run(tmp_path, "approve", f"spec:{spec['id']}", "--path", str(tmp_path))
    task = json.loads(
        _run(
            tmp_path, "decompose", str(spec["id"]), "--title", "do the thing",
            "--criteria", "ok", "--criteria-mode", "auto", "--path", str(tmp_path),
        ).stdout
    )
    _run(tmp_path, "approve", f"gate2:{project['id']}", "--path", str(tmp_path))
    return tmp_path, project, task


def test_run_cli_completes_a_ready_task_unattended(tmp_path: Path):
    tmp_path, project, task = _setup_ready_task(tmp_path)
    result = _run(tmp_path, "run", "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert len(out["processed"]) == 1
    assert out["processed"][0]["outcome"] == "done"

    show = json.loads(_run(tmp_path, "show", str(task["id"]), "--path", str(tmp_path)).stdout)
    assert show["node"]["status"] == "done"


def test_run_cli_refuses_parallel_2_in_default_branch_mode(tmp_path: Path):
    """v4 section 6 Delta C: `--parallel N > 1` is refused unless
    `worktree_mode = "per_node"` -- `init_repo`'s default config.toml
    uses `worktree_mode = "branch"` (v4 section 2's own documented
    default), so this must fail cleanly, not silently degrade to N=1."""
    tmp_path, project, task = _setup_ready_task(tmp_path)
    result = _run(tmp_path, "run", "--parallel", "2", "--path", str(tmp_path))
    assert result.returncode != 0
    assert "per_node" in result.stderr
    # refused before the runner did anything: the ready task is untouched
    show = json.loads(_run(tmp_path, "show", str(task["id"]), "--path", str(tmp_path)).stdout)
    assert show["node"]["status"] == "ready"


def test_run_cli_allows_parallel_2_in_per_node_worktree_mode(tmp_path: Path, monkeypatch):
    from conftest import git_init_with_commit

    tmp_path, project, task = _setup_ready_task(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))  # per_node worktrees live under ~/.muvue
    git_init_with_commit(tmp_path)
    config_path = tmp_path / ".muvue" / "config.toml"
    config_path.write_text(config_path.read_text().replace(
        'worktree_mode = "branch"', 'worktree_mode = "per_node"',
    ).replace('worktree_setup = "uv sync"', 'worktree_setup = ""'))
    result = _run(tmp_path, "run", "--parallel", "2", "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert len(out["processed"]) == 1


def test_run_cli_refuses_per_node_outside_git(tmp_path: Path):
    tmp_path, project, task = _setup_ready_task(tmp_path)
    config_path = tmp_path / ".muvue" / "config.toml"
    config_path.write_text(config_path.read_text().replace(
        'worktree_mode = "branch"', 'worktree_mode = "per_node"',
    ))
    result = _run(tmp_path, "run", "--path", str(tmp_path))
    assert result.returncode != 0
    assert "needs a git repository" in result.stderr


def test_run_cli_parallel_1_works_in_default_branch_mode(tmp_path: Path):
    """`--parallel 1` (or omitted) is never restricted -- only N > 1 is
    (v4 section 6)."""
    tmp_path, project, task = _setup_ready_task(tmp_path)
    result = _run(tmp_path, "run", "--parallel", "1", "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert len(out["processed"]) == 1


def test_handoff_cli_reassigns_owner(tmp_path: Path):
    tmp_path, project, task = _setup_ready_task(tmp_path)
    _run(tmp_path, "start", str(task["id"]), "--owner", "runner:fake", "--path", str(tmp_path))
    result = _run(tmp_path, "handoff", str(task["id"]), "--to", "human-jane", "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["node"]["owner"] == "human-jane"
    assert out["node"]["status"] == "in_progress"


def test_merge_cli_is_a_noop_in_light_mode(tmp_path: Path):
    tmp_path, project, task = _setup_ready_task(tmp_path)
    _run(tmp_path, "start", str(task["id"]), "--owner", "runner:fake", "--path", str(tmp_path))
    _run(tmp_path, "done", str(task["id"]), "--owner", "runner:fake", "--path", str(tmp_path))
    result = _run(tmp_path, "merge", str(task["id"]), "--path", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "no_worktree"
