"""v4 section 4a / P0.5 acceptance #1: "p95 < 60 ms, p99 < 120 ms cold,
on CI hardware" -- measured here as real cold-subprocess wall-clock
time, not estimated. Each iteration spawns a fresh interpreter (`python
-S -m muvue._hook NAME`) since "cold interpreter" is the entire point
of the budget; a warm/reused process would not measure what the budget
is about.

CI runs this file on GitHub's `ubuntu-latest` runners
(`.github/workflows/ci.yml`), which is the "CI hardware" the budget
refers to.
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


@pytest.fixture(scope="module")
def edit_payload(repo) -> str:
    """An Edit on an in_progress node: PreToolUse's worst case, the full
    read-only DB open plus query (v4 section 4a's one DB-reading path
    that runs per tool call)."""
    import json

    from muvue.core import db as core_db
    from muvue.core import nodes, projects

    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        p = projects.create_project(conn, goal="bench")
        projects.set_phase(conn, p["id"], "executing")
        n = nodes.create_node(conn, project_id=p["id"], kind="task", title="t", status="ready")
        nodes.start(conn, n["id"], owner="bench")
    finally:
        conn.close()
    return json.dumps({"tool_name": "Edit", "tool_input": {}, "node_id": n["id"]})


def _time_one_invocation(repo_root: Path, name: str = "pre-push", stdin: str = "") -> float:
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-S", "-m", "muvue._hook", name, str(repo_root)],
        cwd=SRC_DIR,
        capture_output=True,
        text=True,
        input=stdin,
    )
    elapsed = (time.perf_counter() - start) * 1000.0
    assert result.returncode == 0, result.stderr
    return elapsed


def test_hook_fast_path_cold_latency_p95_p99(repo):
    samples = sorted(_time_one_invocation(repo) for _ in range(ITERATIONS))
    p50 = _percentile(samples, 0.5)
    p95 = _percentile(samples, 0.95)
    p99 = _percentile(samples, 0.99)

    # Printed unconditionally (not just on failure) so `pytest -s`/CI
    # logs carry the real numbers regardless of pass/fail -- working
    # rule 9: "report that rather than weakening the criterion."
    print(
        f"\nmuvue._hook cold latency over {ITERATIONS} iterations: "
        f"min={samples[0]:.1f}ms p50={p50:.1f}ms "
        f"p95={p95:.1f}ms p99={p99:.1f}ms max={samples[-1]:.1f}ms"
    )

    assert p95 < 60.0, f"p95={p95:.1f}ms exceeds the 60ms budget (samples={samples})"
    assert p99 < 120.0, f"p99={p99:.1f}ms exceeds the 120ms budget (samples={samples})"


def test_pre_tool_use_db_path_cold_latency(repo, edit_payload):
    """The DB-reading PreToolUse path (Edit/Write). Its budget is the
    section 4a hard deadline: 150 ms, after which it fails open -- so a
    p99 above that would mean checks are routinely skipped."""
    samples = sorted(
        _time_one_invocation(repo, "pre-tool-use", edit_payload) for _ in range(ITERATIONS)
    )
    p95 = _percentile(samples, 0.95)
    p99 = _percentile(samples, 0.99)
    print(
        f"\nPreToolUse (Edit, DB path) cold latency over {ITERATIONS} iterations: "
        f"p50={_percentile(samples, 0.5):.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms"
    )
    assert p99 < 150.0, f"p99={p99:.1f}ms exceeds the 150ms deadline (samples={samples})"


def test_gate_median_added_latency_per_agent_tool_call(repo, edit_payload):
    """v4 section 11's gate row (between P3 and P4): "median added latency
    per agent tool call < 100 ms; otherwise tighten adapters before P4."
    This criterion is new in v4 (v3's gate only had the 80%-logged
    criterion, already passed -- see docs/decisions.md/CHANGELOG for that
    prior gate-pass record).

    `PreToolUse` fires once per agent tool call (v4 section 4a), and its
    added cost to that tool call IS the hook's own cold-subprocess
    execution time -- measured on the worst case, an Edit that opens
    the DB. A
    fresh, independent sample set (not reusing the p95/p99 test's
    samples) so this assertion's own report is self-contained per
    working rule 9 ("verify and report", not "assume an adjacent
    measurement satisfies a differently-worded criterion")."""
    samples = sorted(
        _time_one_invocation(repo, "pre-tool-use", edit_payload) for _ in range(ITERATIONS)
    )
    median = _percentile(samples, 0.5)

    print(
        f"\ngate check -- median added latency per agent tool call over "
        f"{ITERATIONS} cold `muvue._hook` invocations: {median:.1f}ms "
        f"(bar: < 100ms; samples min={samples[0]:.1f}ms max={samples[-1]:.1f}ms)"
    )

    assert median < 100.0, (
        f"median={median:.1f}ms exceeds the gate's 100ms bar (samples={samples})"
    )
