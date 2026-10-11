"""Read-only metrics from a muvue repo's database and the shim's log."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

BLOCKED_RE = re.compile(r"^\s*Blocked\b|\bno commit", re.IGNORECASE)
APPROVAL_TYPES = {"spec.approved", "gate2.approved", "review.approved", "review.auto_approved", "node.done"}
CHECK_OUTPUT_KEYS = ("output", "stdout", "stderr", "tail")


def _connect(repo: Path) -> sqlite3.Connection:
    db = repo / ".muvue" / "muvue.db"
    if not db.exists():
        raise FileNotFoundError(f"no muvue database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def node_ids_with_status(repo: Path, status: str) -> list[int]:
    with _connect(repo) as c:
        rows = c.execute(
            "SELECT id FROM nodes WHERE status = ? AND kind IN ('task', 'subtask') AND deleted_at IS NULL ORDER BY id",
            (status,),
        ).fetchall()
    return [r[0] for r in rows]


def read_shim_log(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _payload(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def check_output_of(event_type: str, payload: dict) -> bool:
    """True when `event_type`/`payload` carries recorded check output: any
    event whose type contains "check" and whose payload has one of
    "output"/"stdout"/"stderr"/"tail", or a `node.review` event whose
    payload's "checks" list contains a dict with one of those keys."""
    if "check" in event_type and any(k in payload for k in CHECK_OUTPUT_KEYS):
        return True
    if event_type == "node.review":
        for check in payload.get("checks") or []:
            if isinstance(check, dict) and any(k in check for k in CHECK_OUTPUT_KEYS):
                return True
    return False


def node_was_flagged(repo: Path, node_id: int) -> bool:
    """True if `node_id`'s event history includes a failed auto-check or a
    flagged review wait -- used to catch an approval that rubber-stamped
    past either."""
    with _connect(repo) as c:
        rows = c.execute("SELECT type, payload FROM events WHERE node_id = ?", (node_id,)).fetchall()
    for etype, raw in rows:
        if etype == "review.auto_check_failed":
            return True
        if etype == "review.awaiting" and _payload(raw).get("flagged") is True:
            return True
    return False


def first_event_actor(repo: Path, event_type: str) -> str | None:
    """`"<actor>/<actor_evidence>"` for the first recorded event of
    `event_type`, or None if there isn't one."""
    with _connect(repo) as c:
        row = c.execute(
            "SELECT actor, actor_evidence FROM events WHERE type = ? ORDER BY id LIMIT 1", (event_type,)
        ).fetchone()
    return f"{row[0]}/{row[1]}" if row is not None else None


def _is_ancestor(repo: Path, sha: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo, capture_output=True)
    return result.returncode == 0


def done_unmerged_nodes(repo: Path, conn: sqlite3.Connection, done_ids: list[int]) -> list[int]:
    """Done nodes with at least one linked commit (`node_commits`) whose
    sha is not an ancestor of the checked-out HEAD -- i.e. muvue called the
    node done but never actually merged its work in."""
    if not done_ids:
        return []
    shas_by_node: dict[int, list[str]] = {}
    rows = conn.execute(
        f"SELECT node_id, sha FROM node_commits WHERE node_id IN ({','.join('?' * len(done_ids))})", done_ids
    ).fetchall()
    for node_id, sha in rows:
        shas_by_node.setdefault(node_id, []).append(sha)
    return sorted(
        node_id for node_id, shas in shas_by_node.items() if any(not _is_ancestor(repo, sha) for sha in shas)
    )


def score_muvue(repo: Path, shim_log: Path | None) -> dict:
    with _connect(repo) as c:
        tasks = c.execute(
            "SELECT id, status, summary FROM nodes WHERE kind IN ('task', 'subtask') AND deleted_at IS NULL"
        ).fetchall()
        commits = Counter(r[0] for r in c.execute("SELECT node_id FROM node_commits"))
        events = c.execute("SELECT type, actor, actor_evidence, payload FROM events").fetchall()
        usage = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(in_tokens), 0), COALESCE(SUM(out_tokens), 0), COALESCE(SUM(cost), 0) FROM node_usage"
        ).fetchone()
        done = [t for t in tasks if t[1] == "done"]
        unmerged = done_unmerged_nodes(repo, c, [t[0] for t in done])

    with_commit = [t for t in done if commits[t[0]] > 0]
    claiming_blocked = [t for t in done if BLOCKED_RE.search(t[2] or "")]
    truthful = [t for t in with_commit if not BLOCKED_RE.search(t[2] or "")]

    approvals: dict[str, Counter] = {}
    for etype, actor, evidence, _ in events:
        if etype in APPROVAL_TYPES or etype.endswith(".approved"):
            approvals.setdefault(etype, Counter())[f"{actor}/{evidence}"] += 1

    log = read_shim_log(shim_log)
    return {
        "truth": {
            "label": "measured",
            "done": len(done),
            "done_with_commit": len(with_commit),
            "done_claiming_blocked": len(claiming_blocked),
            "truth_score": round(len(truthful) / len(done), 3) if done else None,
            "check_runs": sum(1 for e in events if e[0] == "node.review"),
            "check_runs_with_output": sum(1 for e in events if check_output_of(e[0], _payload(e[3]))),
            "check_runs_with_output_label": "heuristic",
            "approvals_by_actor": {k: dict(v) for k, v in sorted(approvals.items())},
        },
        "outcome": {
            "label": "measured",
            "statuses": dict(Counter(t[1] for t in tasks)),
            "failed": sum(1 for t in tasks if t[1] == "failed"),
            "subtasks": _count_subtasks(repo),
            "conflict_events": sum(1 for e in events if "conflict" in e[0]),
            "done_unmerged": len(unmerged),
            "done_unmerged_node_ids": unmerged,
        },
        "tokens": {
            "sessions": int(usage[0]), "sessions_label": "measured",
            "prompt_bytes": [e["prompt_bytes"] for e in log], "prompt_bytes_label": "measured",
            "shim_outcomes": dict(Counter(e["outcome"] for e in log)),
            "in_tokens": int(usage[1]), "out_tokens": int(usage[2]), "cost_usd": round(float(usage[3]), 4),
            "usage_label": "replayed",
        },
    }


def _count_subtasks(repo: Path) -> int:
    with _connect(repo) as c:
        return c.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'subtask' AND deleted_at IS NULL").fetchone()[0]
