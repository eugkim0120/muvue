"""score_muvue reads a real muvue repo: a fake-agent task that is done
with no commit lowers the truth score."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bench.score import check_output_of, node_ids_with_status, read_shim_log, score_muvue


def _env(tmp_path: Path) -> dict:
    (tmp_path / "home").mkdir(exist_ok=True)
    return {**os.environ, "HOME": str(tmp_path / "home"),
            "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
            "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com"}


def _muvue(env: dict, *args: str):
    out = subprocess.run([sys.executable, "-m", "muvue", *args], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return out.stdout


def test_fake_agent_done_without_commit_is_caught(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "start"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    p = str(repo)
    _muvue(env, "init", p)
    cfg = repo / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace('test = "pytest -q"', 'test = "true"').replace('lint = "ruff check ."', 'lint = "true"'))
    pid = _muvue(env, "project", "create", "--goal", "g", "--path", p)["id"]
    sid = _muvue(env, "spec", str(pid), "--title", "s", "--body", "b", "--path", p)["id"]
    _muvue(env, "approve", f"spec:{sid}", "--path", p)
    _muvue(env, "decompose", str(sid), "--title", "t", "--criteria", "c", "--criteria-mode", "auto",
           "--predicted-touches", "src/a.py", "--path", p)
    _muvue(env, "approve", f"gate2:{pid}", "--path", p)
    _muvue(env, "run", "--path", p)

    result = score_muvue(repo, None)
    truth = result["truth"]
    assert truth["label"] == "measured"
    assert truth["done"] == 1
    assert truth["done_with_commit"] == 0
    assert truth["truth_score"] == 0.0
    assert truth["check_runs"] == 1
    assert result["tokens"]["sessions"] == 1
    assert result["tokens"]["usage_label"] == "replayed"
    assert node_ids_with_status(repo, "done") == [2]


def test_read_shim_log(tmp_path):
    log = tmp_path / "shim.jsonl"
    log.write_text('{"outcome": "blocked", "prompt_bytes": 10}\n{"outcome": "committed", "prompt_bytes": 12}\n')
    assert [e["outcome"] for e in read_shim_log(log)] == ["blocked", "committed"]
    assert read_shim_log(None) == []


def test_check_output_of_matches_check_event_with_output_key():
    assert check_output_of("review.auto_check_failed", {"command": "pytest", "stdout": "FAILED x"})
    assert check_output_of("node.check_ran", {"output": "ok"})
    assert check_output_of("check.tail_recorded", {"tail": "...\n"})


def test_check_output_of_matches_node_review_with_checks_list():
    payload = {"tier": "low", "checks": [{"name": "test"}, {"name": "lint", "stderr": "boom"}]}
    assert check_output_of("node.review", payload)


def test_check_output_of_negative_cases():
    assert not check_output_of("review.auto_check_failed", {"command": "pytest"})
    assert not check_output_of("node.review", {"tier": "low", "flagged": False})
    assert not check_output_of("review.awaiting", {"tier": "low", "flagged": True})
    assert not check_output_of("node.done", {"output": "ignored, no 'check' in event type"})
