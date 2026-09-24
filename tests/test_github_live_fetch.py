"""Real GitHub-API wiring for `import`/`merge --pr --create` (plan section
6/11 P6, "needs human verification later"): `core.github` shells out to a
real, already-authenticated `gh` CLI (see docs/decisions.md). Subprocess
calls are mocked here for a deterministic, network-independent test
suite -- `gh`'s own auth/network path is exercised for real by the CLI
smoke test noted in the session report, not by pytest."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from muvue.core import github


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_fetch_issue_via_gh_parses_issue_view_json():
    payload = {"number": 5, "title": "t", "url": "https://github.com/o/r/issues/5", "body": "b"}
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))) as run:
        result = github.fetch_issue_via_gh(5, repo="o/r")
    assert result == payload
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "issue", "view"]
    assert "--repo" in args and "o/r" in args


def test_fetch_issue_via_gh_falls_back_to_pr_view():
    payload = {"number": 8, "title": "pr title", "url": "https://x/8", "body": ""}
    responses = [
        _completed(stderr="issue not found", returncode=1),
        _completed(stdout=json.dumps(payload)),
    ]
    with patch("subprocess.run", side_effect=responses) as run:
        result = github.fetch_issue_via_gh(8)
    assert result == payload
    assert run.call_args_list[0][0][0][:3] == ["gh", "issue", "view"]
    assert run.call_args_list[1][0][0][:3] == ["gh", "pr", "view"]


def test_fetch_issue_via_gh_raises_when_both_fail():
    with patch("subprocess.run", return_value=_completed(stderr="no network", returncode=1)):
        with pytest.raises(github.GithubError):
            github.fetch_issue_via_gh(1)


def test_create_pr_via_gh_returns_url():
    with patch("subprocess.run", return_value=_completed(stdout="https://github.com/o/r/pull/9\n")) as run:
        result = github.create_pr_via_gh(head="node-3", base="main", title="t", body="b", repo="o/r")
    assert result == {"url": "https://github.com/o/r/pull/9"}
    args = run.call_args[0][0]
    assert args[:3] == ["gh", "pr", "create"]
    assert "--head" in args and "node-3" in args
    assert "--repo" in args and "o/r" in args


def test_create_pr_via_gh_raises_on_failure():
    with patch("subprocess.run", return_value=_completed(stderr="no commits between main and node-3", returncode=1)):
        with pytest.raises(github.GithubError):
            github.create_pr_via_gh(head="node-3", base="main", title="t", body="b")


def test_gh_binary_missing_raises_github_error():
    with patch("subprocess.run", side_effect=FileNotFoundError("gh")):
        with pytest.raises(github.GithubError):
            github.fetch_issue_via_gh(1)
