"""`close` (plan section 9 "Structure layer and drift", P6, human verb):
"`close` proposes a structure diff (new/changed components, decisions,
promoted lessons), capped per close, reviewed in the dashboard or as the
PR diff of `components.json`/`decisions.json`, which muvue commits on
`main` as the single writer. Project events exported to `.muvue/history/`."

Two-phase: `preview_close` (also `close_project(..., confirm=False)`,
the CLI/API dry-run) computes the diff without mutating anything;
`close_project(..., confirm=True)` commits it -- writes the new
`components`/`decisions` rows, dumps the full current tables to
`.muvue/components.json`/`.muvue/decisions.json`, commits those two
files on the repo's checked-out `main` (muvue is "the single writer" of
these files, plan section 2/9 -- not a strict-mode airlock merge; there
is no per-project git branch for structure metadata to land on, only the
repo the human already has open), flips the project to `closed`, and
exports its event history (`core/history.py`).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from . import events as events_mod
from . import history as history_mod
from . import projects as projects_mod

# "capped per close" (plan section 9) -- one project's close proposes at
# most this many new rows per category (decisions / promoted lessons /
# components) in a single diff. No sizing guidance in the plan beyond
# "capped"; 20 keeps a PR-diff-sized review reviewable in one sitting
# without silently dropping a close that produced more candidates than
# that (see docs/decisions.md) -- it stays a module attribute, not a
# local constant, so a caller (or a test) can override it.
MAX_DIFF_ITEMS = 20

# Same reasoning, same pattern, as `core.merge`'s `_MERGE_COMMIT_ENV` /
# `_run_git_as_muvue`: a muvue-initiated commit on the human's own
# checkout must not depend on the ambient environment having
# `user.name`/`user.email` configured. Kept local to this module rather
# than added to `core.strict._run_git`, for the same "narrower is the
# smaller, correct change" reason `core/merge.py` gives.
_CLOSE_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "muvue", "GIT_AUTHOR_EMAIL": "muvue@localhost",
    "GIT_COMMITTER_NAME": "muvue", "GIT_COMMITTER_EMAIL": "muvue@localhost",
}


class CloseError(Exception):
    pass


class HumanOnly(CloseError):
    """`close` is a human verb (plan section 4: "Never exposed over
    MCP"), enforced in core itself -- same duplicated-per-module pattern
    as `core.nodes.HumanOnly` / `core.gates.HumanOnly` / `core.asks.HumanOnly`."""


def _require_human(actor: str) -> None:
    if actor != "human":
        raise HumanOnly(
            f"only a human may perform this action (actor was {actor!r}); "
            "human verbs are never exposed over MCP (plan section 4)"
        )


def _run_git_as_muvue(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **_CLOSE_COMMIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def _closeable_gate(conn: sqlite3.Connection, project_id: int) -> list[int]:
    """The plan doesn't specify a precise "closeable" gate -- the
    simplest reading consistent with section 9's framing (a close is a
    project *wrap-up*, proposing structure learned from finished work) is
    "every one of this project's live (non-soft-deleted) `task`/`subtask`
    nodes is `done`". `spec` nodes are excluded from this check the same
    way `core.runner` excludes them from scheduling (docs/decisions.md
    #47): a `ready` spec means "Gate 1 approved, decomposed" -- it never
    transitions to `done` itself, only the tasks decomposed under it do,
    so requiring it to be `done` too would make every project permanently
    unclosable. Returns the offending node ids, empty if closeable."""
    rows = conn.execute(
        "SELECT id FROM nodes WHERE project_id = ? AND deleted_at IS NULL "
        "AND kind IN ('task', 'subtask') AND status != 'done' "
        "ORDER BY id",
        (project_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _decision_candidates(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT n.id AS note_id, n.node_id, n.text FROM notes n "
        "JOIN nodes nd ON nd.id = n.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL AND n.kind = 'decision' "
        "ORDER BY n.id LIMIT ?",
        (project_id, MAX_DIFF_ITEMS),
    ).fetchall()
    out = []
    for r in rows:
        text = r["text"] or ""
        first_line = text.strip().splitlines()[0] if text.strip() else f"decision (node {r['node_id']})"
        out.append({
            "title": first_line[:120],
            "context": None,
            "choice": text,
            "source_node_id": r["node_id"],
        })
    return out


def _promoted_lesson_candidates(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """"a lesson note with pinned=true ... are 'promoted lessons'" (plan
    section 9). A lesson's `text` is the JSON blob `core.nodes.fail`
    writes (trigger/failure/do_instead/scope) -- promoted the same shape
    decisions already use: title <- trigger, context <- failure, choice
    <- do_instead (see docs/decisions.md: promoted lessons land in the
    `decisions` table too, no separate structure-layer table for them)."""
    rows = conn.execute(
        "SELECT n.id AS note_id, n.node_id, n.text FROM notes n "
        "JOIN nodes nd ON nd.id = n.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL AND n.kind = 'lesson' AND n.pinned = 1 "
        "ORDER BY n.id LIMIT ?",
        (project_id, MAX_DIFF_ITEMS),
    ).fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["text"])
        except (json.JSONDecodeError, TypeError):
            data = {}
        title = data.get("trigger") or f"lesson (node {r['node_id']})"
        out.append({
            "title": f"[lesson] {title}"[:120],
            "context": data.get("failure"),
            "choice": data.get("do_instead"),
            "source_node_id": r["node_id"],
        })
    return out


def _component_candidates(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """No static-analysis/anchor-hashing scan exists yet (that's the
    structure-drift machinery, P7's `audit`) -- the only structural
    signal already on hand this early is each node's own
    `predicted_touches` path globs (plan section 3), so a candidate
    component is proposed per distinct glob this project touched, not
    already tracked (see docs/decisions.md)."""
    rows = conn.execute(
        "SELECT DISTINCT pt.path_glob, nd.title FROM predicted_touches pt "
        "JOIN nodes nd ON nd.id = pt.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL "
        "ORDER BY pt.path_glob",
        (project_id,),
    ).fetchall()
    by_glob: dict[str, list[str]] = {}
    for r in rows:
        by_glob.setdefault(r["path_glob"], []).append(r["title"])
    existing = {r["name"] for r in conn.execute("SELECT name FROM components").fetchall()}
    out = []
    for glob, titles in by_glob.items():
        if len(out) >= MAX_DIFF_ITEMS:
            break
        if glob in existing:
            continue
        purpose = "; ".join(dict.fromkeys(titles))[:200]
        out.append({"name": glob, "kind": "path", "purpose": purpose})
    return out


def preview_close(conn: sqlite3.Connection, project_id: int) -> dict:
    """Compute (without mutating) whether `project_id` can close and what
    its proposed structure diff would be -- the dashboard/CLI
    `close --dry-run` / `GET /projects/{id}/close-preview` surface."""
    project = projects_mod.get_project(conn, project_id)
    blockers = _closeable_gate(conn, project_id)
    diff = {
        "decisions": _decision_candidates(conn, project_id),
        "promoted_lessons": _promoted_lesson_candidates(conn, project_id),
        "components": _component_candidates(conn, project_id),
    }
    return {
        "project": dict(project),
        "closeable": not blockers,
        "blocking_nodes": blockers,
        "diff": diff,
    }


def _write_structure_json(conn: sqlite3.Connection, repo_root: Path) -> tuple[Path, Path]:
    components = [dict(r) for r in conn.execute("SELECT * FROM components ORDER BY id").fetchall()]
    decisions = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()]
    muvue_dir = Path(repo_root) / ".muvue"
    muvue_dir.mkdir(parents=True, exist_ok=True)
    comp_path = muvue_dir / "components.json"
    dec_path = muvue_dir / "decisions.json"
    comp_path.write_text(json.dumps(components, indent=2, default=str) + "\n")
    dec_path.write_text(json.dumps(decisions, indent=2, default=str) + "\n")
    return comp_path, dec_path


def close_project(
    conn: sqlite3.Connection,
    project_id: int,
    repo_root: str | Path,
    *,
    actor: str = "human",
    confirm: bool = False,
) -> dict:
    """`confirm=False` (default): dry-run, same shape as `preview_close`
    plus `"confirmed": False`, no mutation at all. `confirm=True`: commits
    the diff (raises `CloseError` if not closeable), writes and commits
    `components.json`/`decisions.json` on `main`, flips the project to
    `closed`, and exports its event history."""
    _require_human(actor)
    preview = preview_close(conn, project_id)
    if not confirm:
        return {"confirmed": False, **preview}

    if not preview["closeable"]:
        raise CloseError(
            f"project {project_id} is not closeable: nodes not done: {preview['blocking_nodes']}"
        )

    repo_root = Path(repo_root)

    for c in preview["diff"]["components"]:
        cur = conn.execute(
            "INSERT INTO components (name, kind, purpose, anchors_json, status) "
            "VALUES (?, ?, ?, '[]', 'current')",
            (c["name"], c["kind"], c["purpose"]),
        )
        row = conn.execute("SELECT * FROM components WHERE id = ?", (cur.lastrowid,)).fetchone()
        events_mod.record_event(
            conn, project_id=project_id, node_id=None, actor=actor,
            type_="component.created", payload=dict(row),
        )

    for d in preview["diff"]["decisions"] + preview["diff"]["promoted_lessons"]:
        cur = conn.execute(
            "INSERT INTO decisions (title, context, choice, rejected_json, status, source_node_id) "
            "VALUES (?, ?, ?, '[]', 'current', ?)",
            (d["title"], d.get("context"), d.get("choice"), d.get("source_node_id")),
        )
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (cur.lastrowid,)).fetchone()
        events_mod.record_event(
            conn, project_id=project_id, node_id=d.get("source_node_id"), actor=actor,
            type_="decision.created", payload=dict(row),
        )
    conn.commit()

    comp_path, dec_path = _write_structure_json(conn, repo_root)

    add = _run_git_as_muvue("add", "--", str(comp_path), str(dec_path), cwd=repo_root)
    if add.returncode != 0:
        raise CloseError(f"failed to stage structure diff: {add.stderr}")
    commit = _run_git_as_muvue(
        "commit", "-q", "-m", f"muvue: close project {project_id} structure diff",
        cwd=repo_root,
    )
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise CloseError(f"failed to commit structure diff: {commit.stderr}")

    project_row = projects_mod.set_phase(conn, project_id, "closed", actor=actor)

    history_path = history_mod.export_project(conn, project_id, repo_root)

    return {
        "confirmed": True,
        "project": dict(project_row),
        "diff_committed": preview["diff"],
        "components_path": str(comp_path),
        "decisions_path": str(dec_path),
        "history_path": str(history_path),
    }
