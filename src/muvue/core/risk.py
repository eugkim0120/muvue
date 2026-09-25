"""Risk-tier computation (plan section 5): the single source of truth for
diff size, path globs, deletions and criteria-edit signals.

"Structure-graph signals (touched invariants, external interfaces) added
once components exist" (plan section 5) -- P3+, not implemented here.

P0/P1 track no line-level diff, only `predicted_touches` (a set of path
globs a node is expected to touch) and, once committed, `node_commits.files`
(a JSON list of file paths actually touched). Neither carries an added/
removed line count. Per the P2 prompt ("diff size (from predicted_touches
count or actual node_commits diff if available -- predicted_touches is what
P0/P1 already track, use that)"), diff size is approximated by touch count
against `planning.max_files_per_task` (medium) and `risk.max_diff_lines`
(high) -- see docs/decisions.md for why file-count is read as the proxy
for both thresholds.

Deletions have no producer yet (no git-diff capture exists before P3's
hooks/structure layer): `has_deletions` is accepted as an explicit
parameter so the signal is wired into the one tier function now, but
nothing currently sets it to True. Documented as a decision, not a bug.
"""

from __future__ import annotations

import fnmatch
import sqlite3

from . import db as db_mod
from .config import MuvueConfig

TIERS = ("low", "medium", "high")


def _rank(tier: str) -> int:
    return TIERS.index(tier)


def max_tier(a: str, b: str) -> str:
    """The more severe of two tiers. Used to ensure a tier recomputed from
    fresh diff signals never *downgrades* a tier a human or a criteria
    edit already raised (plan P2 acceptance #4: criteria edit never
    auto-approves)."""
    return a if _rank(a) >= _rank(b) else b


def _node_touches(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        row["path_glob"]
        for row in db_mod.query_all(
            conn,
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?",
            (node_id,),
        )
    ]


def _actual_touches(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        row["path"]
        for row in db_mod.query_all(
            conn,
            "SELECT path FROM actual_touches WHERE node_id = ?",
            (node_id,),
        )
    ]


def touches_outside_predicted(conn: sqlite3.Connection, node_id: int) -> bool:
    """v4 section 5 risk-tier input: "touches outside `predicted_touches`".
    `actual_touches` (written from real commits by
    `core.hooks.handle_post_commit`) is compared against the node's
    `predicted_touches` globs. True if at least one real touch matches
    none of the predicted globs; False if there are no actual touches
    recorded yet (nothing to compare -- not a signal either way) or every
    actual touch is covered by some predicted glob."""
    predicted = _node_touches(conn, node_id)
    actual = _actual_touches(conn, node_id)
    if not actual:
        return False
    return any(not any(fnmatch.fnmatch(path, glob) for glob in predicted) for path in actual)


def touches_globs(touches: list[str], globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for path in touches for glob in globs)


_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_FILE_GLOBS = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "*_spec.*", "conftest.py")


def is_test_touch(path: str) -> bool:
    """Plan section 5, 'done -> review': "Diffs touching test files or
    criteria are always flagged." Test-shaped means a test directory
    segment (`tests/`, `__tests__/`, `spec/`, ...) or a conventional test
    file name (`test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `conftest.py`).
    Matched per path segment, never as a substring: `latest.py` and
    `attestation.py` are not tests."""
    parts = path.replace("\\", "/").lower().split("/")
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in _TEST_FILE_GLOBS)


def compute_tier(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    config: MuvueConfig,
    *,
    criteria_edited: bool = False,
    has_deletions: bool = False,
    touches_outside_predicted: bool = False,
) -> str:
    """Compute a node's risk tier from diff size, path globs, deletions,
    criteria edits, and touches outside `predicted_touches` (plan section
    5). `criteria_edited=True` unconditionally forces `high` -- this is
    the one place that rule lives; `core.gates.edit_criteria` calls
    through here instead of special-casing `high` itself.

    `touches_outside_predicted` (v4 section 5, new input) only ever
    *raises* the tier, never lowers it -- callers pass the result of
    `touches_outside_predicted()` above. It's only meaningful once real
    commits exist (`actual_touches` is populated), so it's a signal at
    `done`/review time (`core.nodes.done`), not at Gate 2 approval, when
    no commit history exists yet to compare against. Per plan section
    13's residual-risk statement ("`predicted_touches` is a heuristic...
    not a safety guarantee"), this is scored, not hard-blocked: it bumps
    the tier at least to `medium`, same severity class as exceeding
    `max_files_per_task`, without overriding a higher tier some other
    signal already produced."""
    if criteria_edited:
        return "high"
    if has_deletions:
        return "high"

    touches = _node_touches(conn, node["id"])
    if touches_globs(touches, config.risk.globs):
        return "high"

    n_touches = len(touches)
    if n_touches > config.risk.max_diff_lines:
        tier = "high"
    elif n_touches > config.planning.max_files_per_task:
        tier = "medium"
    else:
        tier = "low"

    if touches_outside_predicted:
        tier = max_tier(tier, "medium")
    return tier


def is_flagged(conn: sqlite3.Connection, node: sqlite3.Row, config: MuvueConfig) -> bool:
    """"Diffs touching test files or criteria are always flagged" (plan
    section 5) -- read here as: any predicted touch looks test-shaped, or
    the node's `risk_tier` is already `high` because of a criteria edit
    (see `core.gates.edit_criteria`). Overrides low-tier auto-approval
    regardless of tier."""
    touches = _node_touches(conn, node["id"])
    if any(is_test_touch(t) for t in touches):
        return True
    return False
