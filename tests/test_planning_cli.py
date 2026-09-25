"""Dogfood-gate follow-up: CLI verbs for the planning surface (plan section
4) that previously existed only as raw `core` calls -- `muvue project
create`, `muvue spec`, `muvue decompose`. See docs/decisions.md #39.

Drives the whole planning-to-execution loop through subprocess CLI calls
only (no raw `core.*` calls), proving the gap the dogfood run found is
closed.
"""

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


def test_project_create_via_cli(tmp_path: Path):
    init_repo(tmp_path)
    result = _run(
        tmp_path, "project", "create", "--goal", "ship the thing", "--path", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["goal"] == "ship the thing"
    assert out["phase"] == "planning"


def test_spec_via_cli_creates_pending_spec_node(tmp_path: Path):
    init_repo(tmp_path)
    project = json.loads(
        _run(tmp_path, "project", "create", "--goal", "g", "--path", str(tmp_path)).stdout
    )
    result = _run(
        tmp_path, "spec", str(project["id"]),
        "--title", "the spec", "--body", "spec body text", "--path", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    node = json.loads(result.stdout)
    assert node["kind"] == "spec"
    assert node["status"] == "pending"
    assert node["title"] == "the spec"
    assert node["body_md"] == "spec body text"


def test_decompose_via_cli_creates_pending_task_under_spec(tmp_path: Path):
    init_repo(tmp_path)
    project = json.loads(
        _run(tmp_path, "project", "create", "--goal", "g", "--path", str(tmp_path)).stdout
    )
    spec = json.loads(
        _run(
            tmp_path, "spec", str(project["id"]), "--title", "s", "--body", "b",
            "--path", str(tmp_path),
        ).stdout
    )
    _run(tmp_path, "approve", f"spec:{spec['id']}", "--path", str(tmp_path))

    result = _run(
        tmp_path, "decompose", str(spec["id"]),
        "--title", "do the thing", "--criteria", "passes tests",
        "--predicted-touches", "a.py", "--criteria-mode", "auto",
        "--path", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    task = json.loads(result.stdout)
    assert task["kind"] == "task"
    assert task["status"] == "pending"
    assert task["parent_id"] == spec["id"]
    assert task["criteria_mode"] == "auto"


def test_decompose_is_agent_verb_refused_for_human_actor_is_not_applicable():
    # decompose wraps core.nodes.create_node, which has no actor
    # restriction (agent verb, not human-only) -- see core.gates.submit_spec
    # for the same pattern. Nothing to assert here beyond documentation;
    # the real human/agent split is exercised by test_gates.py's HumanOnly
    # tests on approve_spec/approve_node/approve_gate2 already.
    pass


def test_approve_review_target_via_cli(tmp_path: Path):
    """`approve review:ID` (core.nodes.approve_review) discoverable via the
    CLI, not just the API -- docs/decisions.md #39."""
    init_repo(tmp_path)
    path_args = ["--path", str(tmp_path)]
    project = json.loads(
        _run(tmp_path, "project", "create", "--goal", "g", *path_args).stdout
    )
    spec = json.loads(
        _run(
            tmp_path, "spec", str(project["id"]), "--title", "s", "--body", "b", *path_args
        ).stdout
    )
    _run(tmp_path, "approve", f"spec:{spec['id']}", *path_args)
    task = json.loads(
        _run(
            tmp_path, "decompose", str(spec["id"]), "--title", "manual task",
            "--criteria", "human checks this", "--criteria-mode", "manual", *path_args,
        ).stdout
    )
    _run(tmp_path, "approve", f"gate2:{project['id']}", *path_args)
    _run(tmp_path, "start", str(task["id"]), "--owner", "agent-1", *path_args)
    done_result = _run(
        tmp_path, "done", str(task["id"]), "--owner", "agent-1", "--summary", "s", *path_args
    )
    assert json.loads(done_result.stdout)["node"]["status"] == "review"

    review_result = _run(tmp_path, "approve", f"review:{task['id']}", *path_args)
    assert review_result.returncode == 0, review_result.stderr
    assert json.loads(review_result.stdout)["node"]["status"] == "done"


def test_full_planning_to_execution_loop_via_cli_only(tmp_path: Path):
    """The actual gate-passing proof: project create -> spec -> approve
    spec -> decompose -> approve gate2 -> start -> done, entirely through
    `muvue` subprocess calls. No raw `core.*` call anywhere in this test."""
    init_repo(tmp_path)
    path_args = ["--path", str(tmp_path)]

    project = json.loads(
        _run(tmp_path, "project", "create", "--goal", "dogfood loop", *path_args).stdout
    )
    project_id = project["id"]

    spec = json.loads(
        _run(
            tmp_path, "spec", str(project_id), "--title", "spec", "--body", "body",
            *path_args,
        ).stdout
    )

    approve_spec_result = _run(tmp_path, "approve", f"spec:{spec['id']}", *path_args)
    assert approve_spec_result.returncode == 0, approve_spec_result.stderr

    task = json.loads(
        _run(
            tmp_path, "decompose", str(spec["id"]), "--title", "do it",
            "--criteria", "passes tests", "--predicted-touches", "a.py",
            "--criteria-mode", "auto", *path_args,
        ).stdout
    )

    approve_gate2_result = _run(tmp_path, "approve", f"gate2:{project_id}", *path_args)
    assert approve_gate2_result.returncode == 0, approve_gate2_result.stderr
    gate2_out = json.loads(approve_gate2_result.stdout)
    assert task["id"] in gate2_out["approved_node_ids"]

    start_result = _run(tmp_path, "start", str(task["id"]), "--owner", "agent-1", *path_args)
    assert start_result.returncode == 0, start_result.stderr

    done_result = _run(
        tmp_path, "done", str(task["id"]), "--owner", "agent-1", "--summary", "done",
        *path_args,
    )
    assert done_result.returncode == 0, done_result.stderr
    done_out = json.loads(done_result.stdout)
    assert done_out["node"]["status"] == "done"
