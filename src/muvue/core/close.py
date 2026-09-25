"""`close` (plan section 9 "Structure layer and drift", P6, human verb):
"`close` proposes a structure diff (new/changed components, decisions,
promoted lessons), capped per close, reviewed in the dashboard or as the
PR diff of `components.json`/`decisions.json`."

Two-phase: `preview_close` (also `close_project(..., confirm=False)`,
the CLI/API dry-run) computes the diff without mutating anything;
`close_project(..., confirm=True)` commits it -- writes the new
`components`/`decisions` rows, then builds a commit updating
`.muvue/components.json`/`.muvue/decisions.json` to the full current
tables.

**v4 section 9: "Structure commits never touch a checked-out branch
directly."** v3 (P6) had this commit land straight onto whatever branch
`repo_root` had checked out ("on `main`"), racing the user's own working
tree and index lock. Instead: the commit is built with a temporary git
index (`GIT_INDEX_FILE`, see `_write_structure_commit`) onto a dedicated
`STRUCTURE_REF` (`refs/heads/muvue/structure`), never touching
`repo_root`'s real `.git/index` or working tree. Only *after* that
commit exists does `close_project` decide whether it's safe to move
`main`: `_maybe_fast_forward_main` fast-forwards `main` (via `git merge
--ff-only`, which updates HEAD, the index and the working tree together)
when `main` is the checked-out branch *and* the tree is clean; otherwise
it leaves `main` and the working tree completely untouched and records
an unacked `inbox.structure_update_ready` event describing where the
structure commit landed, for the user to merge/PR by hand. Single-writer
invariant preserved -- muvue is still the only thing that ever writes
`components`/`decisions` content; only *how* the git commit reaches the
user's checkout changed (see docs/decisions.md).

Finally flips the project to `closed` and exports its event history
(`core/history.py`).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from . import actor as actor_mod
from . import db as db_mod
from . import events as events_mod
from . import gitutil as gitutil_mod
from . import history as history_mod
from . import projects as projects_mod

# v4 section 9's dedicated structure ref. A plain branch (not a tag or a
# detached commit floating with no ref) so `git log`/`git merge --ff-only`
# and every other ordinary git command a human runs against their own repo
# just works against it, the same way `core.strict`'s per-node `node-<id>`
# branches do.
STRUCTURE_REF = "refs/heads/muvue/structure"

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


def _require_human(actor: str, actor_evidence: str | None = None) -> None:
    try:
        actor_mod.require_human(actor, actor_evidence)
    except actor_mod.HumanOnly as e:
        raise HumanOnly(str(e)) from None

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
    rows = db_mod.query_all(
        conn,
        "SELECT id FROM nodes WHERE project_id = ? AND deleted_at IS NULL "
        "AND kind IN ('task', 'subtask') AND status != 'done' "
        "ORDER BY id",
        (project_id,),
    )
    return [r["id"] for r in rows]


def _decision_candidates(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = db_mod.query_all(
        conn,
        "SELECT n.id AS note_id, n.node_id, n.text FROM notes n "
        "JOIN nodes nd ON nd.id = n.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL AND n.kind = 'decision' "
        "ORDER BY n.id LIMIT ?",
        (project_id, MAX_DIFF_ITEMS),
    )
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
    rows = db_mod.query_all(
        conn,
        "SELECT n.id AS note_id, n.node_id, n.text FROM notes n "
        "JOIN nodes nd ON nd.id = n.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL AND n.kind = 'lesson' AND n.pinned = 1 "
        "ORDER BY n.id LIMIT ?",
        (project_id, MAX_DIFF_ITEMS),
    )
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
    rows = db_mod.query_all(
        conn,
        "SELECT DISTINCT pt.path_glob, nd.title FROM predicted_touches pt "
        "JOIN nodes nd ON nd.id = pt.node_id "
        "WHERE nd.project_id = ? AND nd.deleted_at IS NULL "
        "ORDER BY pt.path_glob",
        (project_id,),
    )
    by_glob: dict[str, list[str]] = {}
    for r in rows:
        by_glob.setdefault(r["path_glob"], []).append(r["title"])
    existing = {r["name"] for r in db_mod.query_all(conn, "SELECT name FROM components")}
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


def _structure_file_contents(conn: sqlite3.Connection) -> dict[str, str]:
    """The full current `components`/`decisions` tables, serialized to the
    same JSON text `.muvue/components.json`/`.muvue/decisions.json` have
    always held -- but purely in memory. Unlike the v3-era
    `_write_structure_json` this replaces, nothing here touches disk:
    writing these paths into `repo_root`'s actual working tree before
    knowing whether a fast-forward will happen would itself be exactly
    the kind of surprise mutation v4 section 9 is calling out (a
    just-created-and-abandoned untracked/modified file if the ff turns
    out to be unsafe). `_write_structure_commit` blobs this content
    straight into git's object database instead (`git hash-object -w
    --stdin`)."""
    components = [dict(r) for r in db_mod.query_all(conn, "SELECT * FROM components ORDER BY id")]
    decisions = [dict(r) for r in db_mod.query_all(conn, "SELECT * FROM decisions ORDER BY id")]
    return {
        ".muvue/components.json": json.dumps(components, indent=2, default=str) + "\n",
        ".muvue/decisions.json": json.dumps(decisions, indent=2, default=str) + "\n",
    }


def _resolve(repo_root: Path, revision: str) -> str | None:
    result = _run_git_as_muvue("rev-parse", "--verify", revision, cwd=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _write_structure_commit(repo_root: Path, files: dict[str, str], message: str) -> str:
    """Build a commit that updates exactly `files` (repo-relative path ->
    new full text content) relative to whatever was already there,
    landing it on `STRUCTURE_REF` -- entirely through a temporary index
    (`GIT_INDEX_FILE`), so `repo_root`'s real `.git/index` and working
    tree are never read from or written to by this function. Returns the
    new commit sha.

    Base tree / parent commit: `STRUCTURE_REF`'s current tip if the ref
    already exists, else `repo_root`'s current `HEAD` (the first-ever
    structure commit). Choosing the *commit*, not just the tree, as the
    parent gives `STRUCTURE_REF` real ancestry back to a point on the
    branch that created it -- which is exactly what later lets
    `_maybe_fast_forward_main`'s `git merge --ff-only` succeed without
    any separate `merge-base --is-ancestor` bookkeeping here.

    `GIT_INDEX_FILE` points at a fresh `tempfile.mkstemp()` path, unique
    per call, removed again once (successfully or not) it's no longer
    needed -- never a shared fixed path -- so two concurrent calls (or
    one racing a manual git operation) never share, and can't corrupt,
    the same temporary index. The single-writer invariant on
    `STRUCTURE_REF` itself is then enforced by `update-ref`'s own
    compare-and-swap (old value = whatever this call read `STRUCTURE_REF`
    as, moments before) rather than assumed.
    """
    head_sha = _resolve(repo_root, "HEAD")
    if head_sha is None:
        raise CloseError(f"{repo_root} has no HEAD commit to base a structure commit on")
    structure_sha = _resolve(repo_root, STRUCTURE_REF)
    parent_sha = structure_sha or head_sha
    base_tree = f"{parent_sha}^{{tree}}"

    fd, tmp_index = tempfile.mkstemp(prefix="muvue-structure-index-")
    os.close(fd)
    os.remove(tmp_index)  # must not pre-exist: `read-tree` writes a fresh index file itself
    env = {**os.environ, **_CLOSE_COMMIT_ENV, "GIT_INDEX_FILE": tmp_index}

    def _git(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True,
            env=env, input=input_text,
        )

    try:
        read = _git("read-tree", base_tree)
        if read.returncode != 0:
            raise CloseError(f"failed to seed temporary index from {base_tree}: {read.stderr}")

        for path, content in files.items():
            hash_obj = _git("hash-object", "-w", "--stdin", input_text=content)
            if hash_obj.returncode != 0:
                raise CloseError(f"failed to write blob for {path}: {hash_obj.stderr}")
            blob_sha = hash_obj.stdout.strip()
            update = _git("update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{path}")
            if update.returncode != 0:
                raise CloseError(f"failed to stage {path} in temporary index: {update.stderr}")

        write_tree = _git("write-tree")
        if write_tree.returncode != 0:
            raise CloseError(f"failed to write structure tree: {write_tree.stderr}")
        new_tree_sha = write_tree.stdout.strip()

        commit = _git("commit-tree", new_tree_sha, "-p", parent_sha, "-m", message)
        if commit.returncode != 0:
            raise CloseError(f"failed to create structure commit: {commit.stderr}")
        new_commit_sha = commit.stdout.strip()
    finally:
        if os.path.exists(tmp_index):
            os.remove(tmp_index)

    old_value = structure_sha if structure_sha else ""
    update_ref = _run_git_as_muvue(
        "update-ref", STRUCTURE_REF, new_commit_sha, old_value, cwd=repo_root,
    )
    if update_ref.returncode != 0:
        raise CloseError(f"failed to advance {STRUCTURE_REF} to {new_commit_sha}: {update_ref.stderr}")
    return new_commit_sha


def _maybe_fast_forward_main(repo_root: Path, structure_sha: str) -> dict:
    """v4 section 9: fast-forward `main` only when it's the checked-out
    branch *and* the working tree is clean -- otherwise `main` and the
    working tree are left completely untouched (see `close_project`'s
    inbox-item fallback). `git merge --ff-only`, not a raw `git
    update-ref refs/heads/main <sha>`: `main` is the branch `repo_root`'s
    own HEAD is actually pointing at here, so a raw ref move would
    advance the branch pointer while leaving the checked-out files and
    index stale against the new tip (an instant false-dirty `git
    status`). `merge --ff-only` updates HEAD, the index and the working
    tree together as one real git operation, and refuses outright (exit
    nonzero, no partial effect) if fast-forwarding isn't actually
    possible -- which also means it's the ancestry check, no separate
    `merge-base --is-ancestor` needed (docs/decisions.md)."""
    branch = gitutil_mod.current_branch(repo_root)
    if branch != "main":
        return {"fast_forwarded": False, "reason": f"checked-out branch is {branch!r}, not main"}
    status = _run_git_as_muvue("status", "--porcelain", cwd=repo_root)
    if status.stdout.strip() != "":
        return {"fast_forwarded": False, "reason": "working tree is dirty"}
    merge = _run_git_as_muvue("merge", "--ff-only", STRUCTURE_REF, cwd=repo_root)
    if merge.returncode != 0:
        return {"fast_forwarded": False, "reason": f"not a fast-forward: {merge.stderr.strip()}"}
    return {"fast_forwarded": True, "sha": structure_sha}


def close_project(
    conn: sqlite3.Connection,
    project_id: int,
    repo_root: str | Path,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
    confirm: bool = False,
) -> dict:
    """`confirm=False` (default): dry-run, same shape as `preview_close`
    plus `"confirmed": False`, no mutation at all. `confirm=True`: commits
    the diff (raises `CloseError` if not closeable), writes and commits
    `components.json`/`decisions.json` on `main`, flips the project to
    `closed`, and exports its event history."""
    _require_human(actor, actor_evidence)
    preview = preview_close(conn, project_id)
    if not confirm:
        return {"confirmed": False, **preview}

    if not preview["closeable"]:
        raise CloseError(
            f"project {project_id} is not closeable: nodes not done: {preview['blocking_nodes']}"
        )

    repo_root = Path(repo_root)

    with db_mod.write_txn(conn):
        for c in preview["diff"]["components"]:
            cur = conn.execute(
                "INSERT INTO components (name, kind, purpose, anchors_json, status) "
                "VALUES (?, ?, ?, '[]', 'current')",
                (c["name"], c["kind"], c["purpose"]),
            )
            row = conn.execute("SELECT * FROM components WHERE id = ?", (cur.lastrowid,)).fetchone()
            events_mod.record_event(
                conn, project_id=project_id, node_id=None, actor=actor, actor_evidence=actor_evidence,
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
                actor_evidence=actor_evidence,
                type_="decision.created", payload=dict(row),
            )

    files = _structure_file_contents(conn)
    comp_path = repo_root / ".muvue" / "components.json"
    dec_path = repo_root / ".muvue" / "decisions.json"

    structure_sha = _write_structure_commit(
        repo_root, files, f"muvue: close project {project_id} structure diff",
    )
    ff_result = _maybe_fast_forward_main(repo_root, structure_sha)

    inbox_event_id = None
    if not ff_result["fast_forwarded"]:
        with db_mod.write_txn(conn):
            inbox_event_id = events_mod.record_event(
                conn, project_id=project_id, node_id=None, actor=actor, actor_evidence=actor_evidence,
                type_="inbox.structure_update_ready",
                payload={
                    "ref": STRUCTURE_REF,
                    "sha": structure_sha,
                    "reason": ff_result["reason"],
                    "message": (
                        f"structure update for project {project_id} committed on "
                        f"{STRUCTURE_REF} ({structure_sha[:12]}); not fast-forwarded onto "
                        f"main ({ff_result['reason']}) -- merge it manually "
                        f"(e.g. `git merge {STRUCTURE_REF}`) or open a PR"
                    ),
                },
            )

    project_row = projects_mod.set_phase(conn, project_id, "closed", actor=actor, actor_evidence=actor_evidence)

    history_path = history_mod.export_project(conn, project_id, repo_root)

    return {
        "confirmed": True,
        "project": dict(project_row),
        "diff_committed": preview["diff"],
        "components_path": str(comp_path),
        "decisions_path": str(dec_path),
        "structure_ref": STRUCTURE_REF,
        "structure_sha": structure_sha,
        "fast_forwarded": ff_result["fast_forwarded"],
        "inbox_event_id": inbox_event_id,
        "history_path": str(history_path),
    }
