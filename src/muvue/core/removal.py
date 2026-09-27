"""Soft-delete a node and its descendants before Gate 2 (project canvas
plan: a task or subtask added by mistake can be removed from the
dashboard, but only while the plan is still being shaped -- once Gate 2
freezes criteria, removing a node needs a plan revision instead, same
as any other post-freeze change)."""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import nodes as nodes_mod


class RemovalError(Exception):
    pass


def _descendants(conn: sqlite3.Connection, node_id: int) -> list[sqlite3.Row]:
    out = []
    frontier = [node_id]
    while frontier:
        rows = db_mod.query_all(
            conn, "SELECT id FROM nodes WHERE parent_id = ? AND deleted_at IS NULL",
            (frontier.pop(),),
        )
        for row in rows:
            out.append(row)
            frontier.append(row["id"])
    return out


def remove_node(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> dict:
    with db_mod.write_txn(conn):
        node = nodes_mod.get_node(conn, node_id)
        to_remove = [node] + [nodes_mod.get_node(conn, d["id"]) for d in _descendants(conn, node_id)]
        # Gate-2 approval must be checked on every node being removed, not
        # just the root: a spec never gets a `criteria_hash` of its own,
        # so removing a spec whose descendant task/subtask was already
        # frozen would otherwise silently destroy that frozen work with
        # no revision trail (final-review Important #2).
        approved = [r["id"] for r in to_remove if r["criteria_hash"] is not None]
        if approved:
            raise RemovalError(
                f"node(s) {approved} are already Gate-2 approved; propose a plan revision "
                "instead of removing them"
            )
        # `nodes.soft_delete` (not a hand-rolled UPDATE) so the recorded
        # `node.deleted` event carries the *full* row snapshot, matching
        # every other node.* event's convention -- `rebuild` replays
        # node.* events as full-row snapshots, so a payload with just
        # {"node_id": ...} (the old behavior here) crashed replay with
        # KeyError: 'id' for any DB that had ever removed a node (final
        # review Critical #1). `write_txn` is reentrant, so nesting is
        # safe.
        for row in to_remove:
            nodes_mod.soft_delete(conn, row["id"], actor=actor, actor_evidence=actor_evidence)
        return {"removed": [r["id"] for r in to_remove]}
