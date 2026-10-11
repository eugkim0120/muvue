"""The shim commits fixture files when its dependencies are present,
reports blocked without committing when they are not, and its output
parses with muvue's Claude stream-json parser."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from bench.shim_agent import stream_json
from bench.suite import load_suite
from muvue.core.drivers import parse_claude_stream_json

SHIM = Path(__file__).resolve().parent.parent / "bench" / "shim_agent.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
    "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _repo(tmp_path: Path) -> Path:
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True, capture_output=True)
    return repo


def _run(repo: Path, tmp_path: Path, prompt: str, task_map: dict, *args: str):
    (tmp_path / "map.json").write_text(json.dumps(task_map))
    env = {**GIT_ENV, "BENCH_SUITE_DIR": str(load_suite("readinglog").root),
           "BENCH_TASK_MAP": str(tmp_path / "map.json"), "BENCH_SHIM_LOG": str(tmp_path / "shim.jsonl")}
    return subprocess.run([sys.executable, str(SHIM), *args], cwd=repo, env=env, input=prompt,
                          capture_output=True, text=True)


def _log(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "shim.jsonl").read_text().splitlines()]


def test_commits_fixture_files_with_trailer(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "You are an agent working on muvue node T2 in this repository.\n", {"2": "models"})
    assert out.returncode == 0, out.stderr
    msg = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True).stdout
    assert "Muvue-Node: 2" in msg
    assert (repo / "src/readinglog/models.py").is_file()
    result = parse_claude_stream_json(out.stdout)
    assert result.status == "done"
    assert result.in_tokens == 10 + 43878 + 227926
    assert result.out_tokens == 1383
    assert _log(tmp_path)[0]["outcome"] == "committed"


def test_missing_dependency_reports_blocked_and_commits_nothing(tmp_path):
    repo = _repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    out = _run(repo, tmp_path, "You are an agent working on muvue node T3 in this repository.\n", {"3": "store"})
    assert out.returncode == 0
    result = parse_claude_stream_json(out.stdout)
    assert result.status == "done"
    assert result.summary.startswith("Blocked. No commit made.")
    assert "src/readinglog/models.py" in result.summary
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout == head
    entry = _log(tmp_path)[0]
    assert entry["outcome"] == "blocked" and entry["missing"] == ["src/readinglog/models.py"]


def test_unmapped_prompt_is_an_error(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "no node here", {})
    assert out.returncode == 1
    assert parse_claude_stream_json(out.stdout).status == "failed"


def test_plain_mode_applies_every_task(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "", {}, "--plain")
    assert out.returncode == 0, out.stderr
    count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert int(count) == 1 + 6
    assert (repo / "src/readinglog/commands/stats.py").is_file()


def test_stream_json_error_flag():
    parsed = parse_claude_stream_json(stream_json("boom", {}, is_error=True))
    assert parsed.status == "failed" and parsed.error == "boom"
