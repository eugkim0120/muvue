"""Node lifecycle: creation and the status-machine verbs (start/done/fail/...).

Every mutating function here is the single write path (plan working rule 3):
the CLI never issues raw SQL. Each mutation appends a full-row-snapshot event
so `rebuild` (see rebuild.py) can reconstruct live state by replay alone.

Every mutating function opens its writes inside `core.db.write_txn` (v4
section 1.2, working rule 3): a nested call (e.g. `fail` calling
`add_note`) is a no-op re-entry into the same, already-open transaction
(see `core.db.write_txn`'s docstring) -- so the whole verb commits or
rolls back atomically, not slice-by-slice.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import actor as actor_mod
from . import db as db_mod
from . import drift, events, gitutil, review, risk, state_machine

DEFAULT_LEASE_MINUTES = 60


class NodeError(Exception):
    pass


class HumanOnly(NodeError):
    """Raised when a non-human actor calls a human verb (plan section 4:
    "Never exposed over MCP"). `core.gates` has its own identically-named
    exception for the same rule applied to gate/revision approvals; this
    one covers review approve/reject, which live in `nodes.py` to avoid a
    `nodes` <-> `gates` import cycle. Enforced in `muvue.core` itself
    (P3 acceptance #4: an adversarial agent script calling a human verb
    directly against core must be refused by core, not just by the
    CLI/API/MCP surface)."""


def _require_human(actor: str, actor_evidence: str | None = None) -> None:
    try:
        actor_mod.require_human(actor, actor_evidence)
    except actor_mod.HumanOnly as e:
        raise HumanOnly(str(e)) from None

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_node(conn: sqlite3.Connection, node_id: int) -> sqlite3.Row:
    row = db_mod.query_one(conn, "SELECT * FROM nodes WHERE id = ?", (node_id,))
    if row is None:
        raise LookupError(f"no such node: {node_id}")
    return row


def create_node(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    kind: str,
    title: str,
    parent_id: int | None = None,
    body_md: str = "",
    criteria: list[str] | None = None,
    criteria_mode: str = "manual",
    risk_tier: str = "low",
    max_attempts: int = 3,
    status: str = "pending",
    actor: str = "human",
    actor_evidence: str = "tty",
    predicted_touches: list[str] | None = None,
    depends_on: list[int] | None = None,
    owner: str | None = None,
) -> sqlite3.Row:
    """`owner` pre-assigns a ready node to someone without a lease (the
    runner routes it to that owner's agent). `depends_on` writes `deps` edges (plan section 3) from the new node
    to live nodes of the same project, each logged as a replayable
    `dep.added` event."""
    criteria = criteria or []
    criteria_json = json.dumps(criteria)
    with db_mod.write_txn(conn):
        # criteria_hash stays NULL until Gate 2 (or a plan-revision
        # re-approval) freezes it -- see core.gates.approve_node. A
        # non-NULL criteria_hash means "this criteria_json was
        # approved"; that's the signal core.gates.edit_criteria uses to
        # detect a post-freeze edit.
        cur = conn.execute(
            "INSERT INTO nodes (project_id, parent_id, kind, title, body_md, status, "
            "criteria_json, criteria_mode, risk_tier, max_attempts, owner) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                parent_id,
                kind,
                title,
                body_md,
                status,
                criteria_json,
                criteria_mode,
                risk_tier,
                max_attempts,
                owner,
            ),
        )
        node_id = cur.lastrowid
        for glob in predicted_touches or []:
            conn.execute(
                "INSERT OR IGNORE INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
                (node_id, glob),
            )
        row = get_node(conn, node_id)
        events.record_event(
            conn,
            project_id=project_id,
            node_id=node_id,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="node.created",
            payload=dict(row),
        )
        for dep_id in depends_on or []:
            dep = get_node(conn, dep_id)
            if dep["project_id"] != project_id or dep["deleted_at"] is not None:
                raise NodeError(
                    f"node {node_id} cannot depend on node {dep_id}: dependencies must be "
                    "live nodes in the same project"
                )
            conn.execute(
                "INSERT OR IGNORE INTO deps (node_id, depends_on) VALUES (?, ?)", (node_id, dep_id)
            )
            events.record_event(
                conn, project_id=project_id, node_id=node_id, actor=actor,
                actor_evidence=actor_evidence, type_="dep.added",
                payload={"node_id": node_id, "depends_on": dep_id},
            )
        return row


def _apply_transition(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    *,
    to_status: str,
    lease_actor: str,
    event_actor_role: str,
    event_type: str,
    request_id: str | None,
    extra_columns: dict | None = None,
    block_reason: str | None = None,
    actor_evidence: str = "subprocess",
) -> sqlite3.Row:
    """`lease_actor` is the identity checked against nodes.owner (plan
    section 3: "only the lease owner may transition a node"). Distinct from
    `event_actor_role`, which is the events.actor CHECK'd role (plan
    section 3: actor IN {agent, human, hook, daemon})."""
    state_machine.validate_transition(
        node["status"], to_status, owner=node["owner"], actor=lease_actor
    )
    columns = {"status": to_status, "block_reason": block_reason}
    if extra_columns:
        columns.update(extra_columns)
    with db_mod.write_txn(conn):
        set_clause = ", ".join(f"{k} = ?" for k in columns)
        conn.execute(
            f"UPDATE nodes SET {set_clause} WHERE id = ?",
            (*columns.values(), node["id"]),
        )
        row = get_node(conn, node["id"])
        events.record_event(
            conn,
            project_id=row["project_id"],
            node_id=row["id"],
            actor=event_actor_role,
            actor_evidence=actor_evidence,
            type_=event_type,
            payload=dict(row),
            request_id=request_id,
        )
        return row


def ready(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        return _apply_transition(
            conn, node, to_status="ready", lease_actor=node["owner"] or "",
            event_actor_role=actor, event_type="node.ready", request_id=None,
            actor_evidence=actor_evidence,
        )


def release_lease(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    actor: str = "daemon",
    actor_evidence: str = "subprocess",
) -> sqlite3.Row:
    """in_progress -> ready with the lease cleared and `attempts` left
    alone: the work was interrupted (a `pause` stopped its runner), not
    failed by the agent."""
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        return _apply_transition(
            conn, node, to_status="ready", lease_actor=owner, event_actor_role=actor,
            event_type="node.released", request_id=None,
            extra_columns={"owner": None, "lease_until": None},
            actor_evidence=actor_evidence,
        )


def to_pending(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    """ready -> pending. Used by Gate 2 criteria-edit re-tiering (P1): a
    frozen node whose criteria change is pulled back out of `ready` until a
    human re-approves it (see core.gates.edit_criteria)."""
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        return _apply_transition(
            conn, node, to_status="pending", lease_actor=node["owner"] or "",
            event_actor_role=actor, event_type="node.pending", request_id=None,
            actor_evidence=actor_evidence,
        )


def await_approval(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    """in_progress -> awaiting_approval. A criteria edit after Gate 2
    parks the running node, lease intact, until a human re-approves
    (v4 section 5); PreToolUse blocks edits meanwhile."""
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        return _apply_transition(
            conn, node, to_status="awaiting_approval", lease_actor=node["owner"] or "",
            event_actor_role=actor, event_type="node.awaiting_approval", request_id=None,
            actor_evidence=actor_evidence,
        )


def resume_approved(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    """awaiting_approval -> in_progress, same owner, after re-approval."""
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        return _apply_transition(
            conn, node, to_status="in_progress", lease_actor=node["owner"] or "",
            event_actor_role=actor, event_type="node.approval_resumed", request_id=None,
            actor_evidence=actor_evidence,
        )


def start(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    actor: str = "agent",
    actor_evidence: str = "tty",
    request_id: str | None = None,
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
    config=None,
    repo_root=None,
) -> dict:
    node = get_node(conn, node_id)
    if node["status"] == "blocked":
        # The state machine's (blocked, in_progress) edge exists for the
        # unblock paths (an answer, a handoff), which check the reason is
        # resolved. `start` doesn't, so without this an agent could lift
        # its own block: resume before `retry_at`, or before its question
        # is answered (v4 section 10, "ignores rate limit").
        raise NodeError(
            f"cannot start node {node_id}: it is blocked ({node['block_reason']}); "
            "it resumes when the block is resolved"
        )
    project = db_mod.query_one(
        conn,
        "SELECT phase, branch FROM projects WHERE id = ?",
        (node["project_id"],),
    )
    if project is not None and project["phase"] in ("planning", "paused"):
        raise NodeError(
            f"cannot start node {node_id}: project {node['project_id']} is "
            f"{project['phase']} (Gate 2 not yet approved, or paused -- plan "
            "section 5 'Emergency stop': pause refuses start)"
        )

    # v4 section 5: branch coherence. "doctor and every start compare
    # current HEAD against the branch recorded at project start. On
    # divergence: warn in light mode, refuse in strict mode." Compared
    # against repo_root's OWN checkout -- never a per-node strict-mode
    # worktree's HEAD, which is intentionally on its own node-<id> branch
    # and would always "diverge" by design; that's not what this check is
    # about (it's "the user checked out an unrelated branch in their own
    # terminal mid-project"). Checked before the strict-mode worktree
    # bind below, so a strict-mode refusal never leaves an orphan
    # worktree behind. A no-op when repo_root wasn't passed (most
    # pre-v4/light call sites, and any test not exercising this) or the
    # project recorded no branch (created without a repo_root).
    if repo_root is not None and project is not None and project["branch"]:
        current_branch = gitutil.current_branch(repo_root)
        if current_branch is not None and current_branch != project["branch"]:
            mode = getattr(config, "mode", "light") if config is not None else "light"
            if mode == "strict":
                raise NodeError(
                    f"cannot start node {node_id}: repo is on branch "
                    f"{current_branch!r}, project {node['project_id']} started on "
                    f"{project['branch']!r} (branch coherence, strict mode refuses "
                    "-- v4 section 5)"
                )
            events.record_event(
                conn,
                project_id=node["project_id"],
                node_id=node_id,
                actor="hook",
                actor_evidence="subprocess",
                type_="branch.diverged",
                payload={
                    "expected_branch": project["branch"],
                    "current_branch": current_branch,
                },
            )
    # P3 acceptance #1: a second driver cannot take an already-leased node.
    # `state_machine.TRANSITIONS` already refuses a second `start` once the
    # node is `in_progress` (there is no (in_progress, in_progress) edge),
    # but that raises a generic InvalidTransition regardless of *why*. This
    # explicit, earlier check gives a precise "leased by someone else"
    # error for the common case -- a different owner racing the same
    # `ready` node, or calling `start` again before the daemon's
    # reconcile-on-start has reverted an expired lease -- instead of
    # silently reassigning the lease.
    if (
        node["status"] == "in_progress"
        and node["owner"] is not None
        and node["owner"] != owner
        and node["lease_until"] is not None
        and node["lease_until"] > datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ):
        raise NodeError(
            f"cannot start node {node_id}: leased by {node['owner']!r} until "
            f"{node['lease_until']} (refused, not reassigned)"
        )
    lease_until = (datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )

    # Strict mode (plan section 5, P4): bind a per-node git worktree
    # before touching the DB at all, so a `worktree_setup` failure fails
    # `start` cleanly -- the node is never left half-transitioned with a
    # worktree binding that didn't actually finish setting up (see
    # core.strict.bind_worktree's docstring). Git mechanics stay outside
    # `write_txn`: it's filesystem/subprocess work, not a DB write, and
    # it must be able to fail *before* any DB transaction opens.
    extra_columns = {"owner": owner, "lease_until": lease_until}
    if config is not None and getattr(config, "mode", "light") == "strict":
        if repo_root is None:
            raise NodeError(
                f"cannot start node {node_id} in strict mode: repo_root is required "
                "to bind a worktree"
            )
        from . import strict as strict_mod

        worktree_path = strict_mod.bind_worktree(node, config, repo_root)
        if node["worktree"] is None:
            extra_columns["worktree"] = str(worktree_path)
    elif (
        config is not None
        and getattr(config, "worktree_mode", "branch") == "per_node"
        and repo_root is not None
    ):
        from . import strict as strict_mod

        worktree_path = strict_mod.bind_light_worktree(node, config, repo_root)
        if node["worktree"] is None:
            extra_columns["worktree"] = str(worktree_path)

    with db_mod.write_txn(conn):
        # Checked inside BEGIN IMMEDIATE so two concurrent calls with the
        # same request id serialise here instead of both applying.
        if request_id and events.find_recent_by_request_id(conn, request_id, "node.start"):
            return {"noop": True, "node": dict(get_node(conn, node_id))}
        node = get_node(conn, node_id)
        row = _apply_transition(
            conn,
            node,
            to_status="in_progress",
            lease_actor=owner,
            event_actor_role="agent",
            event_type="node.start",
            request_id=request_id,
            extra_columns=extra_columns,
            actor_evidence=actor_evidence,
        )
        return {"noop": False, "node": dict(row)}


def bump_version(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    """Optimistic concurrency counter (plan section 3: `nodes.version`).
    Called on human-visible edits made to a node while it may be leased
    out to an agent (`add_note` for human notes/comments,
    `core.gates.edit_criteria` for criteria changes) so a lease holder's
    later `done(..., expected_version=...)` can detect "this node changed
    under me" instead of silently overwriting a human's edit (P3
    acceptance #2)."""
    with db_mod.write_txn(conn):
        conn.execute("UPDATE nodes SET version = version + 1 WHERE id = ?", (node_id,))
        row = get_node(conn, node_id)
        events.record_event(
            conn,
            project_id=row["project_id"],
            node_id=node_id,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="node.version_bumped",
            payload=dict(row),
        )
        return row


class VersionMismatch(NodeError):
    """Raised by `done`/`fail` when `expected_version` no longer matches
    `nodes.version` -- the node was edited (e.g. a human note or criteria
    change) since the caller's `start` (P3 acceptance #2)."""


def done(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    request_id: str | None = None,
    summary: str | None = None,
    config=None,
    expected_version: int | None = None,
    run_checks=None,
    cwd: str = ".",
    actor_evidence: str = "tty",
) -> dict:
    """Mark a node done. Idempotent: a node already `done` is a no-op, and a
    duplicate request_id within the 24h dedupe window is a no-op (plan
    section 4 / working rule: request-id idempotency).

    P0 drove in_progress -> review -> done unconditionally in one call
    (criteria evaluation and human review were P1/P2 scope). P2 (plan
    section 5, "done -> review") adds real gating, but only when `config`
    is passed: with `config=None` (every P0/P1 call site, still exercised
    by tests/test_idempotency.py and tests/test_rebuild_property.py), the
    old unconditional in_progress -> review -> done behavior is preserved
    byte-for-byte -- see docs/decisions.md. When `config` is given (the
    CLI and API do, since both load config on every invocation), the node
    is routed through core.risk: low-tier and unflagged auto-approves
    straight to `done`; anything else (medium/high tier, or a diff that
    touches test files) stops at `review` and needs a human
    `nodes.approve_review`.

    Check commands and `git show` run *before* the write transaction
    opens, so a slow test suite never holds the database write lock.
    """
    checks, stats = run_checks, None
    if config is not None:
        pre = get_node(conn, node_id)
        if pre["status"] == "in_progress":
            if pre["worktree"] and Path(pre["worktree"]).exists():
                from . import hooks  # hooks imports this module
                hooks.link_worktree_commits(conn, node_id, pre["worktree"])
            checks = review.precompute_checks(pre, config, run_checks, cwd)
            shas = risk.node_commit_shas(conn, node_id)
            if shas:
                stats = risk.diff_stats(review.check_cwd(pre, config, cwd) or cwd, shas)

    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        if node["status"] == "done":
            return {"noop": True, "node": dict(node)}

        dup = events.find_recent_by_request_id(conn, request_id, "node.done") if request_id else None
        if dup is not None:
            return {"noop": True, "node": dict(get_node(conn, node_id))}

        if expected_version is not None and node["version"] != expected_version:
            raise VersionMismatch(
                f"cannot mark node {node_id} done: version is {node['version']}, "
                f"expected {expected_version} (edited since start -- re-brief "
                "and retry rather than overwrite)"
            )

        with db_mod.write_txn(conn):
            reviewing = _apply_transition(
                conn, node, to_status="review", lease_actor=owner, event_actor_role="agent",
                event_type="node.review", request_id=None, actor_evidence=actor_evidence,
            )

            if config is None:
                row = _apply_transition(
                    conn,
                    reviewing,
                    to_status="done",
                    lease_actor=owner,
                    event_actor_role="agent",
                    event_type="node.done",
                    request_id=request_id,
                    extra_columns={"lease_until": None, "summary": summary},
                    actor_evidence=actor_evidence,
                )
                return {"noop": False, "node": dict(row), "auto_approved": True}

            # v4 section 5: touches outside predicted_touches is a new
            # tier input, only meaningful once real commits exist
            # (actual_touches, populated by core.hooks.handle_post_commit) --
            # so it's compared here, at done()/review time, not at Gate 2
            # approval (core.gates.approve_node), which has no commit history
            # yet. Never lowers the tier -- see core.risk.compute_tier.
            touches_outside = risk.touches_outside_predicted(conn, node_id)
            tier = risk.max_tier(
                risk.compute_tier(
                    conn, reviewing, config, touches_outside_predicted=touches_outside,
                    diff_lines=stats["lines"] if stats else None,
                    has_deletions=bool(stats and stats["deleted"]),
                ),
                reviewing["risk_tier"],
            )
            flagged = risk.is_flagged(conn, reviewing, config)
            conn.execute("UPDATE nodes SET risk_tier = ? WHERE id = ?", (tier, node_id))
            reviewing = get_node(conn, node_id)

            # Light-mode `review` dispatch (plan section 5, P3): auto/external/
            # manual criteria modes each add their own reason to flag a node to
            # `review`, on top of core.risk's tier/test-touch flag.
            dispatch = review.dispatch(conn, reviewing, config, run_checks=checks, cwd=cwd)
            if dispatch["flag"]:
                flagged = True
                events.record_event(
                    conn, project_id=reviewing["project_id"], node_id=node_id, actor="agent",
                    actor_evidence=actor_evidence,
                    type_=dispatch["event_type"], payload=dispatch["payload"],
                )

            if tier == "low" and not flagged:
                row = _apply_transition(
                    conn,
                    reviewing,
                    to_status="done",
                    lease_actor=owner,
                    event_actor_role="agent",
                    event_type="node.done",
                    request_id=request_id,
                    extra_columns={"lease_until": None, "summary": summary},
                    actor_evidence=actor_evidence,
                )
                events.record_event(
                    conn,
                    project_id=row["project_id"],
                    node_id=node_id,
                    actor="daemon",
                    actor_evidence="subprocess",
                    type_="review.auto_approved",
                    payload={"tier": tier},
                )
                return {"noop": False, "node": dict(row), "auto_approved": True}

            conn.execute("UPDATE nodes SET summary = ? WHERE id = ?", (summary, node_id))
            row = get_node(conn, node_id)
            # `node.`-prefixed so `rebuild.py` picks up the row snapshot with
            # `summary` now set -- `review.awaiting` right below deliberately does
            # *not* start with `node.` (same reason `review.auto_approved` doesn't,
            # see docs/decisions.md), so it alone would leave the replayed
            # `summary` stale (found by P5's rebuild-first test, this was a
            # pre-existing gap since P2 introduced this branch).
            events.record_event(
                conn, project_id=row["project_id"], node_id=node_id, actor="agent",
                actor_evidence=actor_evidence,
                type_="node.summary_recorded", payload=dict(row),
            )
            events.record_event(
                conn,
                project_id=row["project_id"],
                node_id=node_id,
                actor="agent",
                actor_evidence=actor_evidence,
                type_="review.awaiting",
                payload={"tier": tier, "flagged": flagged},
                request_id=request_id,
            )
            return {"noop": False, "node": dict(row), "auto_approved": False}


RUBBER_STAMP_SECONDS = 10


def log_approval_timing(
    conn: sqlite3.Connection,
    node: sqlite3.Row,
    *,
    approval: str,
    since_types: tuple[str, ...],
    actor: str,
    actor_evidence: str,
) -> None:
    """v4 section 5: "Time-to-approve under 10 s on a medium/high node is
    logged as a rubber-stamp signal." Every medium/high approval records
    `metric.approval_timed` (the KPI denominator); one faster than
    `RUBBER_STAMP_SECONDS` also records `metric.rubber_stamp`. Low-tier
    approvals are not timed: fast is fine there. Elapsed time runs from
    the latest event of `since_types`, i.e. when the node started waiting
    for this approval."""
    tier = node["risk_tier"]
    if tier not in ("medium", "high"):
        return
    placeholders = ", ".join("?" for _ in since_types)
    with db_mod.write_txn(conn):
        waiting = conn.execute(
            f"SELECT ts FROM events WHERE node_id = ? AND type IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 1",
            (node["id"], *since_types),
        ).fetchone()
        if waiting is None:
            return
        entered = datetime.strptime(waiting["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
        elapsed = (datetime.now(timezone.utc) - entered).total_seconds()
        payload = {"elapsed_seconds": elapsed, "tier": tier, "approval": approval}
        for type_ in ("metric.approval_timed",) + (
            ("metric.rubber_stamp",) if elapsed < RUBBER_STAMP_SECONDS else ()
        ):
            events.record_event(
                conn, project_id=node["project_id"], node_id=node["id"], actor=actor,
                actor_evidence=actor_evidence, type_=type_, payload=payload,
            )


def approve_review(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    actor: str = "human",
    actor_evidence: str = "tty",
    repo_root=None,
) -> dict:
    """Human approval of a node sitting in `review` (plan section 5,
    "done -> review... manual waits for a human"). Transitions
    review -> done. The state machine's `review -> done` edge requires the
    actor to match `nodes.owner` (the agent's lease) -- a human approver is
    verified by the dashboard/API session token instead (see
    core.daemon.auth), so this passes the node's own `owner` as the
    lease-check identity while recording the *event* under the real actor
    (`actor`, default "human"). Same pattern `core.gates.approve_spec` /
    `approve_node` already use via `nodes.ready`.

    Refuses (`HumanOnly`) if `actor != "human"`.

    Times the approval for the rubber-stamp KPI
    (`log_approval_timing`)."""
    _require_human(actor, actor_evidence)
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        if node["status"] != "review":
            raise NodeError(f"node {node_id} is status={node['status']!r}, not in review")
        row = _apply_transition(
            conn,
            node,
            to_status="done",
            lease_actor=node["owner"] or "",
            event_actor_role=actor,
            event_type="node.done",
            request_id=None,
            extra_columns={"lease_until": None},
            actor_evidence=actor_evidence,
        )
        log_approval_timing(
            conn, row, approval="review", since_types=("node.review",),
            actor=actor, actor_evidence=actor_evidence,
        )
    # Drift loop 3: approving work that touched stale components verifies
    # them at the node's commit (git runs outside the write lock).
    reverified = (
        drift.reverify_touched(conn, node_id, repo_root, actor=actor, actor_evidence=actor_evidence)
        if repo_root is not None else []
    )
    return {"node": dict(row), "reverified_components": reverified}


def reject_review(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    feedback: str,
    actor: str = "human",
    actor_evidence: str = "tty",
) -> dict:
    """Human rejection of a node in `review` (plan section 4 human verb
    `reject --feedback`): review -> in_progress, feedback recorded as a
    `feedback` note so the agent picks it up on its next `brief`/`show`.
    Refuses (`HumanOnly`) if `actor != "human"`."""
    _require_human(actor, actor_evidence)
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        if node["status"] != "review":
            raise NodeError(f"node {node_id} is status={node['status']!r}, not in review")
        row = _apply_transition(
            conn,
            node,
            to_status="in_progress",
            lease_actor=node["owner"] or "",
            event_actor_role=actor,
            event_type="node.rejected",
            request_id=None,
            actor_evidence=actor_evidence,
        )
        add_note(conn, node_id, kind="feedback", text=feedback, actor=actor, actor_evidence=actor_evidence)
        return {"node": dict(row)}


def fail(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    owner: str,
    lesson: str,
    trigger: str = "",
    do_instead: str = "",
    scope: str = "",
    request_id: str | None = None,
    expected_version: int | None = None,
    actor_evidence: str = "tty",
) -> dict:
    """`attempts` counts *agent* failures only (v4 section 3) -- this is
    the one place that increments it. Crash/timeout reclaims go through
    `core.daemon.reconcile_leases` instead, which increments the
    separate `lease_expiries` counter and never routes to `failed` (see
    that function's docstring).

    The lesson is required in full (plan section 3): `lesson` is its
    `failure`, and `trigger`, `do_instead` and `scope` must be non-empty."""
    structured = lesson_text(trigger=trigger, failure=lesson, do_instead=do_instead, scope=scope)
    with db_mod.write_txn(conn):
        dup = events.find_recent_by_request_id(conn, request_id, "node.fail") if request_id else None
        if dup is not None:
            return {"noop": True, "node": dict(get_node(conn, node_id))}

        node = get_node(conn, node_id)
        if expected_version is not None and node["version"] != expected_version:
            raise VersionMismatch(
                f"cannot fail node {node_id}: version is {node['version']}, "
                f"expected {expected_version} (edited since start)"
            )
        attempts = node["attempts"] + 1
        to_status = "failed" if attempts >= node["max_attempts"] else "ready"
        with db_mod.write_txn(conn):
            row = _apply_transition(
                conn,
                node,
                to_status=to_status,
                lease_actor=owner,
                event_actor_role="agent",
                event_type="node.fail",
                request_id=request_id,
                extra_columns={
                    "attempts": attempts,
                    "owner": None if to_status != "in_progress" else owner,
                    "lease_until": None,
                },
                actor_evidence=actor_evidence,
            )
            add_note(
                conn, node_id, kind="lesson", text=structured, actor="agent",
                actor_evidence=actor_evidence,
            )
            return {"noop": False, "node": dict(row)}


def block(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    reason: str,
    actor: str,
    request_id: str | None = None,
    bump_attempts: bool = False,
    event_actor_role: str = "agent",
    actor_evidence: str = "subprocess",
) -> sqlite3.Row:
    """`bump_attempts` (P5, `core.merge`'s conflict handling -- plan
    section 6 "Merging": "node -> blocked(conflict) ... attempts + 1") and
    `event_actor_role` (a daemon-initiated block, e.g. a merge attempt, is
    not an `agent` action) are additive, defaulted to preserve every
    pre-P5 call site's exact behavior."""
    if reason not in state_machine.BLOCK_REASONS:
        raise NodeError(f"unknown block reason: {reason!r}")
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        extra_columns = {"attempts": node["attempts"] + 1} if bump_attempts else None
        row = _apply_transition(
            conn,
            node,
            to_status="blocked",
            lease_actor=actor,
            event_actor_role=event_actor_role,
            event_type="node.blocked",
            request_id=request_id,
            block_reason=reason,
            extra_columns=extra_columns,
            actor_evidence=actor_evidence,
        )
        return row


def handoff(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    new_owner: str,
    actor: str = "human",
    actor_evidence: str = "tty",
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
) -> dict:
    """Human verb (plan section 6 "Handoff", section 4): reassign a node's
    lease to `new_owner` so a different driver -- an interactive session
    taking over from the unattended runner, or vice versa -- can resume
    purely from DB state (plan principle 1: no in-memory handoff needed).

    The node must currently be `in_progress` or `blocked`. A `blocked`
    node is un-blocked back to `in_progress` as part of the same call (the
    `(blocked, in_progress)` edge already requires an owner match, which
    is trivially satisfied here the same way `approve_review` satisfies
    the lease check on a human-driven transition: by passing the node's
    *current* owner, not the new one, as the state machine's `lease_actor`
    -- the state machine only cares that some legitimate transition is
    happening, not who the new owner will be). An already-`in_progress`
    node has no status change to validate, so its owner/lease are updated
    directly. Refuses (`HumanOnly`) if `actor != "human"`."""
    _require_human(actor, actor_evidence)
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        if node["status"] not in ("in_progress", "blocked"):
            raise NodeError(
                f"cannot hand off node {node_id}: status is {node['status']!r}, "
                "must be in_progress or blocked"
            )
        lease_until = (datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        if node["status"] == "blocked":
            row = _apply_transition(
                conn,
                node,
                to_status="in_progress",
                lease_actor=node["owner"] or "",
                event_actor_role=actor,
                event_type="node.handoff",
                request_id=None,
                extra_columns={"owner": new_owner, "lease_until": lease_until},
                actor_evidence=actor_evidence,
            )
        else:
            conn.execute(
                "UPDATE nodes SET owner = ?, lease_until = ? WHERE id = ?",
                (new_owner, lease_until, node_id),
            )
            row = get_node(conn, node_id)
            events.record_event(
                conn,
                project_id=row["project_id"],
                node_id=node_id,
                actor=actor,
                actor_evidence=actor_evidence,
                type_="node.handoff",
                payload=dict(row),
            )
        return {"node": dict(row)}


def soft_delete(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> sqlite3.Row:
    """Soft delete only (plan section 5): nodes with commits attached are
    never hard-deleted; set deleted_at instead."""
    with db_mod.write_txn(conn):
        node = get_node(conn, node_id)
        conn.execute(
            "UPDATE nodes SET deleted_at = ? WHERE id = ?", (_now(), node["id"])
        )
        row = get_node(conn, node_id)
        events.record_event(
            conn,
            project_id=row["project_id"],
            node_id=row["id"],
            actor=actor,
            actor_evidence=actor_evidence,
            type_="node.deleted",
            payload=dict(row),
        )
        return row


LESSON_FIELDS = ("trigger", "failure", "do_instead", "scope")


def lesson_text(*, trigger: str, failure: str, do_instead: str, scope: str) -> str:
    """The stored form of a lesson note: plan section 3, "lessons must
    carry trigger, failure, do_instead, scope". Every field is required."""
    fields = {"trigger": trigger, "failure": failure, "do_instead": do_instead, "scope": scope}
    missing = [k for k in LESSON_FIELDS if not (fields[k] or "").strip()]
    if missing:
        raise ValueError(
            f"a lesson must carry {', '.join(LESSON_FIELDS)}; missing: {', '.join(missing)}"
        )
    return json.dumps(fields)


def _validate_lesson(text: str) -> None:
    try:
        fields = json.loads(text)
    except ValueError:
        fields = None
    if not isinstance(fields, dict):
        raise ValueError(
            f"a lesson note must be JSON carrying {', '.join(LESSON_FIELDS)} "
            "(see `muvue fail --lesson ... --trigger ... --do-instead ... --scope ...`)"
        )
    lesson_text(**{k: str(fields.get(k) or "") for k in LESSON_FIELDS})


def add_note(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    kind: str,
    text: str,
    actor: str = "agent",
    actor_evidence: str = "tty",
    pinned: bool = False,
) -> dict:
    """Notes dedupe by content hash (plan section 4). A `lesson` note must
    be the structured form `lesson_text` produces."""
    if kind == "lesson":
        _validate_lesson(text)
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    with db_mod.write_txn(conn):
        existing = conn.execute(
            "SELECT * FROM notes WHERE node_id = ? AND content_hash = ?",
            (node_id, content_hash),
        ).fetchone()
        if existing is not None:
            return {"noop": True, "note": dict(existing)}
        cur = conn.execute(
            "INSERT INTO notes (node_id, kind, text, content_hash, pinned) "
            "VALUES (?, ?, ?, ?, ?)",
            (node_id, kind, text, content_hash, int(pinned)),
        )
        note_id = cur.lastrowid
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        events.record_event(
            conn,
            project_id=get_node(conn, node_id)["project_id"],
            node_id=node_id,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="note.added",
            payload=dict(row),
        )
        if actor == "human":
            # Plan section 3/5: a human-visible edit made while a node may be
            # leased out bumps `nodes.version`, so the lease holder's `done`
            # can detect it (P3 acceptance #2). Agent-authored notes (lessons,
            # discoveries the agent itself records) are not edits *by someone
            # else* and don't bump it.
            bump_version(conn, node_id, actor=actor, actor_evidence=actor_evidence)
        return {"noop": False, "note": dict(row)}
