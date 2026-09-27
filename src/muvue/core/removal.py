"""Soft-delete a node and its descendants before Gate 2 (project canvas
plan: a task or subtask added by mistake can be removed from the
dashboard, but only while the plan is still being shaped -- once Gate 2
freezes criteria, removing a node needs a plan revision instead, same
as any other post-freeze change)."""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import events
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
        if node["criteria_hash"] is not None:
            raise RemovalError(
                f"node {node_id} is already Gate-2 approved; propose a plan revision instead of removing it"
            )
        to_remove = [node] + [nodes_mod.get_node(conn, d["id"]) for d in _descendants(conn, node_id)]
        for row in to_remove:
            conn.execute(
                "UPDATE nodes SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (row["id"],),
            )
            events.record_event(
                conn, project_id=row["project_id"], node_id=row["id"], actor=actor,
                actor_evidence=actor_evidence, type_="node.removed", payload={"node_id": row["id"]},
            )
        return {"removed": [r["id"] for r in to_remove]}
