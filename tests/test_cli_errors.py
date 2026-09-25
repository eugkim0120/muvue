"""Expected failures of a CLI verb (an illegal transition, an unknown
node, a failing `worktree_setup`) print one `error:` line and exit 1,
instead of a Python traceback. Programming errors still raise."""

from __future__ import annotations

import subprocess
import sys

from muvue.core.repo_init import init_repo


def _cli(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "muvue", *args, "--path", str(repo)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )


def test_unknown_node_is_one_error_line(tmp_path):
    init_repo(tmp_path)
    result = _cli(tmp_path, "start", "999", "--owner", "me")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.strip().startswith("error: ")
    assert "999" in result.stderr


def test_illegal_transition_is_one_error_line(tmp_path):
    init_repo(tmp_path)
    assert _cli(tmp_path, "project", "create", "--goal", "g").returncode == 0
    assert _cli(tmp_path, "spec", "1", "--title", "s", "--body", "b").returncode == 0
    result = _cli(tmp_path, "done", "1", "--owner", "me")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "illegal transition" in result.stderr
