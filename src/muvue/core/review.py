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

from . import db as db_mod
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
        # Every surface (CLI, MCP, API, runner) wires in
        # `default_run_checks`; `core.nodes.done` runs them before its
        # write transaction and hands the results in here. `None` is
        # left for core unit tests that exercise the tier gate alone.
        for command in check_commands(config):
            if not run_checks(command, cwd):
                return {
                    "flag": True,
                    "event_type": "review.auto_check_failed",
                    "payload": {"node_id": node["id"], "command": command},
                }
    return {"flag": False, "event_type": None, "payload": {}}


def check_commands(config: MuvueConfig) -> list[str]:
    """`[checks] test` then `[checks] lint`; an empty command is skipped."""
    return [c for c in (config.checks.test, config.checks.lint) if c.strip()]


def check_cwd(node: sqlite3.Row, config: MuvueConfig, cwd: str) -> str | None:
    """Where `dispatch` runs checks for this node: the checkout in light
    mode, the node's own worktree in strict mode (None if it has none,
    in which case strict dispatch is a no-op)."""
    if config.mode == "strict":
        return node["worktree"]
    return cwd


def precompute_checks(
    node: sqlite3.Row, config: MuvueConfig, run_checks: RunChecks | None, cwd: str
) -> RunChecks | None:
    """Run the check commands now and return a lookup with the same
    signature as `run_checks`. `core.nodes.done` calls this before it
    opens its write transaction, so a slow test suite never holds the
    database write lock."""
    if run_checks is None or node["criteria_mode"] != "auto":
        return run_checks
    where = check_cwd(node, config, cwd)
    if where is None:
        return run_checks
    results = {command: run_checks(command, where) for command in check_commands(config)}
    return lambda command, _cwd: results[command]


def _stale_touched_component_ids(conn: sqlite3.Connection, node: sqlite3.Row) -> list[int]:
    """P7 drift loop item 3 (plan section 9): "Reconcile-on-touch" --
    a node whose predicted globs or committed paths overlap any file a
    currently `stale` component is anchored to must not sail through to
    `done` unreviewed (decision #136, supersedes #61)."""
    globs = [
        row["path_glob"]
        for row in db_mod.query_all(
            conn,
            "SELECT path_glob FROM predicted_touches WHERE node_id = ? "
            "UNION SELECT path FROM actual_touches WHERE node_id = ?",
            (node["id"], node["id"]),
        )
    ]
    if not globs:
        return []
    hits = []
    for row in db_mod.query_all(
        conn,
        "SELECT id, anchors_json FROM components WHERE status = 'stale'",
    ):
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
