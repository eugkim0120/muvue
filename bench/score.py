"""Read-only metrics from a muvue repo's database and the shim's log."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

BLOCKED_RE = re.compile(r"^\s*Blocked\b|\bno commit", re.IGNORECASE)
APPROVAL_TYPES = {"spec.approved", "gate2.approved", "review.approved", "review.auto_approved", "node.done"}


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
            "check_runs_with_output": sum(1 for e in events if "check" in e[0] and "output" in _payload(e[3])),
            "approvals_by_actor": {k: dict(v) for k, v in sorted(approvals.items())},
        },
        "outcome": {
            "label": "measured",
            "statuses": dict(Counter(t[1] for t in tasks)),
            "failed": sum(1 for t in tasks if t[1] == "failed"),
            "subtasks": _count_subtasks(repo),
            "conflict_events": sum(1 for e in events if "conflict" in e[0]),
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
