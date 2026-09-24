"""v4 section 4a / P0.5 acceptance #1: "p95 < 60 ms, p99 < 120 ms cold,
on CI hardware" -- measured here as real cold-subprocess wall-clock
time, not estimated. Each iteration spawns a fresh interpreter (`python
-S -m muvue._hook NAME`) since "cold interpreter" is the entire point
of the budget; a warm/reused process would not measure what the budget
is about.

This machine is not CI hardware -- there is no CI runner available in
this environment. The numbers below are real measurements taken on the
dev machine this session ran on; see the session's final report for
the honest "measured locally, not on a CI runner" caveat plan section
4a's own text anticipates.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muvue.core.repo_init import init_repo

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
ITERATIONS = 60


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("hook_bench")
    init_repo(root)
    return root


def _time_one_invocation(repo_root: Path) -> float:
    start = time.perf_counter()
    subprocess.run(
        [sys.executable, "-S", "-m", "muvue._hook", "pre-push", str(repo_root)],
        cwd=SRC_DIR,
        check=True,
        capture_output=True,
        text=True,
        input="",
    )
    return (time.perf_counter() - start) * 1000.0


def test_hook_fast_path_cold_latency_p95_p99(repo):
    samples = sorted(_time_one_invocation(repo) for _ in range(ITERATIONS))
    p95 = _percentile(samples, 0.95)
    p99 = _percentile(samples, 0.99)

    # Printed unconditionally (not just on failure) so `pytest -s`/CI
    # logs carry the real numbers regardless of pass/fail -- working
    # rule 9: "report that rather than weakening the criterion."
    print(
        f"\nmuvue._hook cold latency over {ITERATIONS} iterations: "
        f"min={samples[0]:.1f}ms p50={_percentile(samples, 0.5):.1f}ms "
        f"p95={p95:.1f}ms p99={p99:.1f}ms max={samples[-1]:.1f}ms"
    )

    assert p95 < 60.0, f"p95={p95:.1f}ms exceeds the 60ms budget (samples={samples})"
    assert p99 < 120.0, f"p99={p99:.1f}ms exceeds the 120ms budget (samples={samples})"
