"""End to end: the bench drives real muvue through the readinglog suite
with the shim, scores both arms and writes the result file."""

from __future__ import annotations

import json
import subprocess

from bench.run import ArmError, Muvue, _env, main, render_markdown, run_bench
from bench.suite import load_suite


def test_run_bench_both_arms(tmp_path):
    result, path = run_bench("readinglog", ("muvue", "plain"), tmp_path)
    assert path.exists() and json.loads(path.read_text()) == result
    plain = result["arms"]["plain"]
    assert plain["status"] == "ok", plain["error"]
    assert (plain["acceptance"]["passed"], plain["acceptance"]["total"]) == (4, 4)
    arm = result["arms"]["muvue"]
    assert arm["status"] in ("ok", "stalled"), arm["error"]
    assert arm["truth"]["done"] >= 1
    assert arm["tokens"]["sessions"] >= 6
    assert arm["flexibility"]["human_verbs"] >= 3
    assert all(isinstance(b, int) and b > 0 for b in arm["tokens"]["prompt_bytes"])
    table = render_markdown(result)
    assert "| truth score |" in table and "| acceptance |" in table

    # Issue 1 regression: nothing ever merging in the muvue arm. Before the
    # fix, muvue init's own .gitignore edit left the checkout dirty, so
    # _merge_into_checkout deferred every merge forever and `muvue merge`
    # printed `[]` -- silently treated as success.
    assert arm["outcome"]["done_unmerged"] == 0, arm["outcome"]["done_unmerged_node_ids"]
    assert arm["merges"]["runs"] or (
        isinstance(arm["merges"]["final"], list) and arm["merges"]["final"]
    ), "nothing merged -- the dirty_checkout bug is back"


def test_muvue_merge_fix_lands_models_py_on_main(tmp_path):
    """The specific commit a mismerged bench used to lose: models.py never
    made it onto the scratch repo's main. Drive the muvue arm directly so
    we can inspect its repo afterwards."""
    from bench.run import run_muvue_arm

    suite = load_suite("readinglog")
    work = tmp_path / "muvue"
    work.mkdir()
    result = run_muvue_arm(suite, work, cap=30)
    assert result["outcome"]["done_unmerged"] == 0, result["error"]
    repo = work / "repo"
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "src/readinglog/models.py" in tracked


def test_main_rejects_unknown_suite(tmp_path, capsys):
    assert main(["run", "--suite", "nope", "--out-dir", str(tmp_path)]) == 2


def test_env_strips_muvue_and_claude_project_dir_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVUE_SOMETHING", "leaky")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/should/not/leak")
    suite = load_suite("readinglog")
    env = _env(tmp_path, suite)
    assert "MUVUE_SOMETHING" not in env
    assert "CLAUDE_PROJECT_DIR" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_muvue_call_raises_on_non_json_stdout_for_json_verbs(tmp_path, monkeypatch):
    suite = load_suite("readinglog")
    env = _env(tmp_path, suite)
    m = Muvue(tmp_path, env, cap=5)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="not json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        m("project", "create", "--goal", "g")
        raised = False
    except ArmError:
        raised = True
    assert raised


def test_muvue_call_redacts_repo_path_in_steps(tmp_path, monkeypatch):
    suite = load_suite("readinglog")
    env = _env(tmp_path, suite)
    repo = tmp_path / "repo"
    m = Muvue(repo, env, cap=5)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    m("init", str(repo), path_flag=False)
    assert all(str(repo) not in str(s) for s in m.steps)
