"""Worktrees of this repository for the dashboard's Agents tab (mission
control phase 1): every worktree of the checkout plus the strict-mode
airlock's node worktrees (`git worktree list` in the checkout does not
show those), each with ahead/behind against its base branch, changed
paths, the node bound to it, its planned files, the live sessions in it,
and a stage.

A git failure never fails the page: one for a whole listing goes in
`errors`, one for a branch goes on that row as `git failed: ...`."""

from __future__ import annotations

import fnmatch
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db as db_mod
from . import gitutil
from . import sessions as sessions_mod
from . import strict as strict_mod
from .config import MuvueConfig

STAGES = ("worktree", "commits", "checks", "review", "merged")


def worktree_stage(*, ahead: int, behind: int, node_status: str | None, checks_running: bool) -> str:
    """Where a branch is on its way to the base: no commits yet, commits,
    node checks running, node in review, merged. git cannot tell a branch
    merged by fast-forward from one created at an older base commit with
    nothing on it; both read as merged."""
    if ahead == 0 and (behind > 0 or node_status == "done"):
        return "merged"
    if node_status == "review":
        return "review"
    if checks_running:
        return "checks"
    if ahead > 0:
        return "commits"
    return "worktree"


def _planned_globs(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        r["path_glob"] for r in db_mod.query_all(
            conn, "SELECT path_glob FROM predicted_touches WHERE node_id = ? ORDER BY path_glob", (node_id,),
        )
    ]


def _row(conn, entry: gitutil.Worktree, *, git_dir: Path, base: str | None, base_sha: str | None,
         base_error: str | None, is_base: bool, node) -> dict:
    row = {
        "path": entry.path, "branch": entry.branch, "head": entry.head, "base": base, "is_base": is_base,
        "ahead": None, "behind": None, "changed_paths": [], "fork_time": None, "tip_time": None,
        "merged": False, "error": None, "session_ids": [], "stage": None,
        "node_id": node["id"] if node else None, "node_title": node["title"] if node else None,
        "node_status": node["status"] if node else None, "planned": [],
    }
    globs = _planned_globs(conn, node["id"]) if node else []
    row["planned"] = [{"glob": g, "touched": False} for g in globs]
    if is_base:
        return row
    if base_error is not None or entry.head is None:
        row["error"] = base_error or "git failed: the worktree has no HEAD"
        return row
    try:
        status = gitutil.branch_status(git_dir, base_sha=base_sha, tip_sha=entry.head)
    except gitutil.GitError as e:
        row["error"] = str(e)
        return row
    checks_running = node is not None and node["status"] == "in_progress" and sessions_mod.launched_run_finished(conn, node["id"])
    stage = worktree_stage(ahead=status.ahead, behind=status.behind, node_status=row["node_status"], checks_running=checks_running)
    row.update(
        ahead=status.ahead, behind=status.behind, changed_paths=list(status.changed_paths),
        fork_time=status.fork_time, tip_time=status.tip_time, stage=stage, merged=stage == "merged",
        planned=[{"glob": g, "touched": any(fnmatch.fnmatch(p, g) for p in status.changed_paths)} for g in globs],
    )
    return row


def _attach_sessions(rows: list[dict], live: list[dict]) -> None:
    """Each live session goes to the deepest worktree containing its
    directory (a worktree inside the checkout beats the checkout)."""
    tops = sorted(((os.path.realpath(r["path"]), r) for r in rows), key=lambda t: len(t[0]), reverse=True)
    for session in live:
        cwd = os.path.realpath(session["cwd"])
        for top, row in tops:
            if cwd == top or cwd.startswith(top + os.sep):
                row["session_ids"].append(session["id"])
                break


def repo_worktrees(conn: sqlite3.Connection, repo_root: Path, config: MuvueConfig, *, now: datetime | None = None) -> dict:
    live = sessions_mod.list_sessions(conn, config.sessions, now=now)
    nodes_by_path = {
        os.path.realpath(r["worktree"]): r for r in db_mod.query_all(
            conn, "SELECT id, title, status, worktree FROM nodes WHERE worktree IS NOT NULL AND deleted_at IS NULL",
        )
    }
    sources: list[tuple[Path, str | None]] = [(Path(repo_root), None)]
    airlock = strict_mod.airlock_path(repo_root)
    if airlock.exists():
        sources.append((airlock, "main"))
    rows: list[dict] = []
    errors: list[str] = []
    for git_dir, base in sources:
        try:
            entries = [e for e in gitutil.list_worktrees(git_dir) if not e.bare]
        except gitutil.GitError as e:
            errors.append(f"{git_dir}: {e}")
            continue
        is_checkout = base is None
        if is_checkout:
            # The main worktree is listed first; its branch is the base.
            base = entries[0].branch if entries else None
        base_sha, base_error = None, None
        if base is None:
            base_error = "git failed: the base checkout is on a detached HEAD"
        else:
            try:
                base_sha = gitutil.rev_parse(git_dir, base)
            except gitutil.GitError as e:
                base_error = str(e)
        for i, entry in enumerate(entries):
            rows.append(_row(
                conn, entry, git_dir=git_dir, base=base, base_sha=base_sha, base_error=base_error,
                is_base=is_checkout and i == 0, node=nodes_by_path.get(os.path.realpath(entry.path)),
            ))
    _attach_sessions(rows, live)
    return {"worktrees": rows, "errors": errors}
