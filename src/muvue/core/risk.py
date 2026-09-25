"""Risk-tier computation (plan section 5): the single source of truth for
diff size, path globs, deletions, criteria edits, touches outside the
prediction, and structure signals.

Diff size is real once a node has commits: `diff_stats` sums `git show
--numstat` over the node's `node_commits` and lists files the commits
deleted. `core.nodes.done` computes it before its write transaction and
passes `diff_lines`/`has_deletions` in. With no commits (Gate 2, or an
agent that never committed) the touch count stands in, as it did before
(docs/decisions.md #127).

Structure signals: touching a component that carries invariants forces
`high`; touching a deprecated component raises to at least `medium`.
"""

from __future__ import annotations

import fnmatch
import json
import sqlite3
import subprocess
from pathlib import Path

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


def diff_stats(repo: str | Path, shas: list[str]) -> dict | None:
    """Lines changed (added + removed) across `shas`, and the files they
    deleted. None if git can't read the commits here (not a repo, or the
    commits live elsewhere), so the caller falls back to touch count.
    Binary files count as zero lines; their size isn't a line signal."""
    lines = 0
    deleted: list[str] = []
    for sha in shas:
        numstat = subprocess.run(
            ["git", "show", "--numstat", "--format=", sha],
            cwd=repo, capture_output=True, text=True,
        )
        if numstat.returncode != 0:
            return None
        for row in numstat.stdout.splitlines():
            added, removed, _path = row.split("\t", 2)
            if added != "-":
                lines += int(added) + int(removed)
        gone = subprocess.run(
            ["git", "show", "--diff-filter=D", "--name-only", "--format=", sha],
            cwd=repo, capture_output=True, text=True,
        )
        if gone.returncode != 0:
            return None
        deleted.extend(p for p in gone.stdout.splitlines() if p)
    return {"lines": lines, "deleted": sorted(set(deleted))}


def node_commit_shas(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        row["sha"]
        for row in db_mod.query_all(
            conn, "SELECT sha FROM node_commits WHERE node_id = ? ORDER BY rowid", (node_id,)
        )
    ]


def _component_paths(anchors_json: str | None) -> list[str]:
    try:
        anchors = json.loads(anchors_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(anchors, dict):
        return list(anchors)
    if isinstance(anchors, list):
        return [a for a in anchors if isinstance(a, str)]
    return []


def structure_tier(conn: sqlite3.Connection, node_id: int) -> str:
    """v4 section 5: "Structure-graph signals (touched invariants,
    external interfaces) added once components exist." A node touches a
    component when one of its predicted globs or actual paths covers one
    of the component's anchor paths."""
    globs = _node_touches(conn, node_id) + _actual_touches(conn, node_id)
    if not globs:
        return "low"
    tier = "low"
    for row in db_mod.query_all(
        conn,
        "SELECT c.id, c.status, c.anchors_json, "
        "(SELECT COUNT(*) FROM invariants i WHERE i.component_id = c.id) AS n_invariants "
        "FROM components c",
    ):
        paths = _component_paths(row["anchors_json"])
        if not paths or not touches_globs(paths, globs):
            continue
        if row["n_invariants"]:
            return "high"
        if row["status"] == "deprecated":
            tier = "medium"
    return tier


def compute_tier(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    config: MuvueConfig,
    *,
    criteria_edited: bool = False,
    has_deletions: bool = False,
    touches_outside_predicted: bool = False,
    diff_lines: int | None = None,
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

    actual = _actual_touches(conn, node["id"])
    n_files = len(actual) if actual else len(touches)
    # Real diff lines when the caller has them; otherwise the touch count
    # stands in for both thresholds, as before (#127).
    size = diff_lines if diff_lines is not None else len(touches)
    if size > config.risk.max_diff_lines:
        tier = "high"
    elif n_files > config.planning.max_files_per_task:
        tier = "medium"
    else:
        tier = "low"

    if touches_outside_predicted:
        tier = max_tier(tier, "medium")
    return max_tier(tier, structure_tier(conn, node["id"]))


def is_flagged(conn: sqlite3.Connection, node: sqlite3.Row, config: MuvueConfig) -> bool:
    """"Diffs touching test files or criteria are always flagged" (plan
    section 5): any predicted or actually committed path looks
    test-shaped. Criteria edits already force `high` (see
    `core.gates.edit_criteria`). Overrides low-tier auto-approval."""
    touches = _node_touches(conn, node["id"]) + _actual_touches(conn, node["id"])
    if any(is_test_touch(t) for t in touches):
        return True
    return False
