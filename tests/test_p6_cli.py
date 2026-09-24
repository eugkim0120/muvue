"""CLI-level smoke tests for P6's `muvue close`, `muvue import`, and
`muvue merge --pr` -- driven entirely through subprocess `muvue`
invocations, the same style as `tests/test_planning_cli.py` /
`tests/test_run_cli.py`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from muvue.core.repo_init import init_repo


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args],
        cwd=repo_root, capture_output=True, text=True,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
        },
    )


def _init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def _done_task_via_cli(repo_root: Path, **create_kwargs) -> dict:
    project = json.loads(
        _run(repo_root, "project", "create", "--goal", "g", "--path", str(repo_root)).stdout
    )
    spec = json.loads(_run(
        repo_root, "spec", str(project["id"]), "--title", "s", "--body", "b",
        "--path", str(repo_root),
    ).stdout)
    _run(repo_root, "approve", f"spec:{spec['id']}", "--path", str(repo_root))
    args = ["decompose", str(spec["id"]), "--title", create_kwargs.get("title", "t"),
            "--criteria", "done", "--path", str(repo_root)]
    task = json.loads(_run(repo_root, *args).stdout)
    _run(repo_root, "approve", f"gate2:{project['id']}", "--path", str(repo_root))
    _run(repo_root, "start", str(task["id"]), "--owner", "a1", "--path", str(repo_root))
    _run(repo_root, "done", str(task["id"]), "--owner", "a1", "--path", str(repo_root))
    # A manual-criteria-mode node always lands in `review` (core.review's
    # light-mode dispatch), never auto-approves straight to `done` --
    # needs an explicit human approval to actually finish (mirrors
    # tests/test_run_cli.py's own setup for the same reason).
    _run(repo_root, "approve", f"review:{task['id']}", "--path", str(repo_root))
    return task


def test_close_cli_dry_run_then_yes(tmp_path: Path):
    repo_root = _init_git_repo(tmp_path)
    init_repo(repo_root)
    task = _done_task_via_cli(repo_root)
    _run(repo_root, "note", str(task["id"]), "--kind", "decision", "--text",
         "use sqlite WAL mode for concurrency", "--path", str(repo_root))
    project_id = json.loads(
        _run(repo_root, "show", str(task["id"]), "--path", str(repo_root)).stdout
    )["node"]["project_id"]

    preview = json.loads(_run(repo_root, "close", str(project_id), "--path", str(repo_root)).stdout)
    assert preview["confirmed"] is False
    assert preview["closeable"] is True

    result = json.loads(
        _run(repo_root, "close", str(project_id), "--yes", "--path", str(repo_root)).stdout
    )
    assert result["confirmed"] is True
    assert result["project"]["phase"] == "closed"
    assert Path(result["components_path"]).exists()
    assert Path(result["decisions_path"]).exists()
    assert Path(result["history_path"]).exists()


def test_import_github_cli(tmp_path: Path):
    repo_root = _init_git_repo(tmp_path)
    init_repo(repo_root)
    task = _done_task_via_cli(repo_root)

    data_path = tmp_path / "issue.json"
    data_path.write_text(json.dumps({
        "number": 42, "title": "example issue", "url": "https://github.com/o/r/issues/42",
    }))
    result = json.loads(_run(
        repo_root, "import", "--from", "github#42", "--node-id", str(task["id"]),
        "--data", str(data_path), "--path", str(repo_root),
    ).stdout)
    assert result["external_ref"]["ext_id"] == "42"
    assert result["external_ref"]["url"] == "https://github.com/o/r/issues/42"


def test_merge_pr_cli_generates_body(tmp_path: Path):
    repo_root = _init_git_repo(tmp_path)
    init_repo(repo_root)
    task = _done_task_via_cli(repo_root)

    result = json.loads(
        _run(repo_root, "merge", str(task["id"]), "--pr", "--path", str(repo_root)).stdout
    )
    assert result["status"] == "no_worktree"  # light mode, plan section 6
    assert "## Acceptance criteria" in result["pr_body"]
    assert "done" in result["pr_body"]
