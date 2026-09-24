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
        for row in conn.execute(
            "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (node_id,)
        )
    ]


def touches_globs(touches: list[str], globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for path in touches for glob in globs)


def is_test_touch(path: str) -> bool:
    """Plan section 5, 'done -> review': "Diffs touching test files or
    criteria are always flagged." A path is a test touch if it matches the
    conventional `**/test_*` glob or otherwise looks test-shaped."""
    lowered = path.lower()
    return (
        fnmatch.fnmatch(path, "**/test_*")
        or fnmatch.fnmatch(path, "test_*")
        or "test" in lowered
    )


def compute_tier(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    config: MuvueConfig,
    *,
    criteria_edited: bool = False,
    has_deletions: bool = False,
) -> str:
    """Compute a node's risk tier from diff size, path globs, deletions,
    and criteria edits (plan section 5). `criteria_edited=True`
    unconditionally forces `high` -- this is the one place that rule
    lives; `core.gates.edit_criteria` calls through here instead of
    special-casing `high` itself."""
    if criteria_edited:
        return "high"
    if has_deletions:
        return "high"

    touches = _node_touches(conn, node["id"])
    if touches_globs(touches, config.risk.globs):
        return "high"

    n_touches = len(touches)
    if n_touches > config.risk.max_diff_lines:
        return "high"
    if n_touches > config.planning.max_files_per_task:
        return "medium"
    return "low"


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
