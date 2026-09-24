"""Light- and strict-mode `review` dispatch (plan section 5): once
`core.risk` has computed a tier/flag (P2), dispatch additionally checks a
node's `criteria_mode`:

- `auto` -- criteria are checked by running `config.checks.test`. Light
  mode runs it in the current checkout (no clean worktree exists there).
  Strict mode (P4) runs it in the node's own bound worktree -- already a
  fresh git checkout by construction -- instead of the caller's `cwd`,
  the "clean-env checks" plan section 5 describes. A failing check always
  flags the node to `review`, regardless of risk tier.
- `external` -- criteria are checked in the agent's own environment (a
  live service, a manual QA step, something muvue can't run itself).
  Always flags to `review`, since core has no way to verify an external
  criterion was actually satisfied.
- `manual` -- always waits for a human; never auto-approves regardless
  of tier.

Strict mode additionally flags any node whose real worktree diff
(`core.strict.worktree_diff_files`) touches a test-shaped path (plan
section 5, "done -> review": "Diffs touching test files or criteria are
always flagged") -- light mode's equivalent check
(`core.risk.is_flagged`) is a `predicted_touches`-based proxy computed
before any code is written; strict mode has a real git diff to check
instead, since every strict-mode node has a real worktree.

Strict-mode dispatch only runs when the node has a bound worktree
(`node["worktree"]` is set by `core.nodes.start`, P4). A strict-mode node
with no bound worktree (never started, or started before P4 existed) is
still a no-op here, same as before P4 -- see
tests/test_light_review.py::test_dispatch_is_a_noop_outside_light_mode.

`run_checks` is injectable (default: a real subprocess call) so tests
don't need a real shell command / repo checkout -- the same pattern
`core.asks.wait`'s injectable `now` already established for testing
time-dependent logic without sleeping for real.
"""

from __future__ import annotations

import json
import subprocess
import sqlite3
from typing import Callable

from . import risk as risk_mod
from .config import MuvueConfig

RunChecks = Callable[[str, str], bool]
DiffFiles = Callable[[str], list[str]]


def default_run_checks(command: str, cwd: str) -> bool:
    """Real check runner: executes `command` (a config-supplied shell
    command, e.g. `checks.test = "pytest -q"` -- project-controlled
    config, not untrusted user input) in `cwd`. Opt-in only (see
    `dispatch`'s docstring) -- CLI/API pass this explicitly; core tests
    pass a fake instead of spawning a real subprocess."""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, capture_output=True)
    except OSError:
        return False
    return result.returncode == 0


def _criteria_mode_result(node: sqlite3.Row, config: MuvueConfig, *, run_checks, cwd) -> dict:
    mode = node["criteria_mode"]
    if mode == "manual":
        return {
            "flag": True,
            "event_type": "review.manual_criteria",
            "payload": {"node_id": node["id"]},
        }
    if mode == "external":
        return {
            "flag": True,
            "event_type": "review.external_flagged",
            "payload": {"node_id": node["id"]},
        }
    if mode == "auto" and run_checks is not None:
        # Only actually runs a check command when the caller opts in by
        # passing `run_checks` (the CLI/API wire in `default_run_checks`,
        # a real subprocess call -- see core.nodes.done). Every existing
        # P0-P2 call site omits it, so this preserves their exact
        # behavior: `core.risk`'s tier/test-touch gate alone still
        # decides auto-criteria nodes when no check runner is wired in.
        passed = run_checks(config.checks.test, cwd)
        if not passed:
            return {
                "flag": True,
                "event_type": "review.auto_check_failed",
                "payload": {"node_id": node["id"], "command": config.checks.test},
            }
    return {"flag": False, "event_type": None, "payload": {}}


def _stale_touched_component_ids(conn: sqlite3.Connection, node: sqlite3.Row) -> list[int]:
    """P7 drift loop item 3 (plan section 9): "Reconcile-on-touch" --
    a node whose `predicted_touches` globs overlap any file a currently
    `stale` component is anchored to must not sail through to `done`
    unreviewed. `node_touches` (the real per-node structure-graph join
    table) has no populated writer yet anywhere in the codebase, so
    `predicted_touches` (already populated since P0) is the overlap
    signal used here, same fallback the P7 prompt itself names -- see
    docs/decisions.md."""
    globs = [
        row["path_glob"]
        for row in conn.execute(
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (node["id"],)
        )
    ]
    if not globs:
        return []
    hits = []
    for row in conn.execute("SELECT id, anchors_json FROM components WHERE status = 'stale'"):
        try:
            anchors = json.loads(row["anchors_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            anchors = {}
        paths = list(anchors.keys()) if isinstance(anchors, dict) else []
        if paths and risk_mod.touches_globs(paths, globs):
            hits.append(row["id"])
    return hits


def dispatch(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    config: MuvueConfig,
    *,
    run_checks: RunChecks | None = None,
    cwd: str = ".",
    diff_files: DiffFiles | None = None,
) -> dict:
    """Returns {"flag": bool, "event_type": str | None, "payload": dict}.
    `flag=True` means: this node must stop at `review` even if
    `core.risk` alone would have auto-approved it."""
    stale_ids = _stale_touched_component_ids(conn, node)
    if stale_ids:
        return {
            "flag": True,
            "event_type": "review.stale_component_touched",
            "payload": {"node_id": node["id"], "component_ids": stale_ids},
        }

    if config.mode == "light":
        return _criteria_mode_result(node, config, run_checks=run_checks, cwd=cwd)

    if config.mode == "strict":
        worktree = node["worktree"]
        if worktree is None:
            # No bound worktree (never started under strict mode, or a
            # pre-P4 node) -- nothing to run cleanly, nothing to diff.
            return {"flag": False, "event_type": None, "payload": {}}

        result = _criteria_mode_result(node, config, run_checks=run_checks, cwd=worktree)
        if result["flag"]:
            return result

        from . import strict as strict_mod

        diff_fn = diff_files or strict_mod.worktree_diff_files
        touched = diff_fn(worktree)
        if any(risk_mod.is_test_touch(f) for f in touched):
            return {
                "flag": True,
                "event_type": "review.test_edit_flagged",
                "payload": {"node_id": node["id"], "files": touched},
            }
        return result

    return {"flag": False, "event_type": None, "payload": {}}
