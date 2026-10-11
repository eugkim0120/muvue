"""End to end: the bench drives real muvue through the readinglog suite
with the shim, scores both arms and writes the result file."""

from __future__ import annotations

import json

from bench.run import main, render_markdown, run_bench


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


def test_main_rejects_unknown_suite(tmp_path, capsys):
    assert main(["run", "--suite", "nope", "--out-dir", str(tmp_path)]) == 2
