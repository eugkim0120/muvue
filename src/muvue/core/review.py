"""Light-mode `review` dispatch (plan section 5): once `core.risk` has
computed a tier/flag (P2), light mode additionally dispatches on a
node's `criteria_mode`:

- `auto` -- criteria are checked by literally running `config.checks.test`
  in the current checkout (light mode has no clean worktree to run it in
  -- that's strict mode / P4's airlock). A failing check always flags
  the node to `review`, regardless of risk tier.
- `external` -- criteria are checked in the agent's own environment (a
  live service, a manual QA step, something muvue can't run itself).
  Always flags to `review`, since core has no way to verify an external
  criterion was actually satisfied.
- `manual` -- always waits for a human; never auto-approves regardless
  of tier.

`run_checks` is injectable (default: a real subprocess call) so tests
don't need a real shell command / repo checkout -- the same pattern
`core.asks.wait`'s injectable `now` already established for testing
time-dependent logic without sleeping for real.
"""

from __future__ import annotations

import subprocess
import sqlite3
from typing import Callable

from .config import MuvueConfig

RunChecks = Callable[[str, str], bool]


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


def dispatch(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    config: MuvueConfig,
    *,
    run_checks: RunChecks | None = None,
    cwd: str = ".",
) -> dict:
    """Returns {"flag": bool, "event_type": str | None, "payload": dict}.
    `flag=True` means: this node must stop at `review` even if
    `core.risk` alone would have auto-approved it. Only applies in light
    mode -- strict mode's airlock-run auto checks are P4 scope."""
    if config.mode != "light":
        return {"flag": False, "event_type": None, "payload": {}}

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
