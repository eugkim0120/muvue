"""The readinglog suite loads, orders its tasks, and its fixture files,
applied in order, pass the hidden acceptance tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bench.suite import load_suite, run_acceptance, task_order

GIT_ENV = {
    "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
    "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def test_suite_loads_six_tasks_with_usage():
    suite = load_suite("readinglog")
    assert [t.key for t in suite.tasks] == ["models", "store", "addlog", "streak", "progress", "stats"]
    assert suite.task("stats").depends_on == ("addlog", "streak")
    assert suite.task("models").usage["cache_creation_input_tokens"] == 43878
    for t in suite.tasks:
        for rel in t.files:
            assert (suite.root / "files" / rel).is_file(), rel
        assert len(t.carries) == len(t.depends_on)


def test_task_order_puts_dependencies_first():
    order = [t.key for t in task_order(load_suite("readinglog"))]
    for t in load_suite("readinglog").tasks:
        for dep in t.depends_on:
            assert order.index(dep) < order.index(t.key)


def test_all_fixture_files_pass_acceptance(tmp_path: Path):
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    for t in task_order(suite):
        for rel in t.files:
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(suite.root / "files" / rel, dst)
    assert run_acceptance(suite, repo) == (4, 4)


def test_start_files_alone_fail_acceptance(tmp_path: Path):
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    passed, total = run_acceptance(suite, repo)
    assert (passed, total) == (0, 4)
