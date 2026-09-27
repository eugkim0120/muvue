"""Per-agent status for the dashboard's Agents panel: which roles an
agent is routed to, what it's working on right now, and what it has
spent -- so "who is doing what" is answerable at a glance, without
reading the event log by hand."""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import spend as spend_mod
from .config import MuvueConfig

ROLES = ("spec", "task", "subtask")


def agent_status(conn: sqlite3.Connection, project_id: int, config: MuvueConfig) -> list[dict]:
    """One row per agent that is either declared under `[agents.*]` or
    routed to a role -- `RoutingConfig`'s defaults (plan section 2) route
    every role to an agent even when `[agents.*]` itself is empty (the
    common case in tests and a freshly-init'd repo), so enumerating
    `config.agents` alone would silently produce an empty list."""
    spend_rows = spend_mod.project_spend(conn, project_id)
    names = set(config.agents) | {
        config.routing.spec, config.routing.task, config.routing.subtask
    }
    out = []
    for name in sorted(names):
        roles = [r for r in ROLES if getattr(config.routing, r) == name]
        # `core.runner.run_node` leases a node to `f"runner:{agent_name}"`,
        # not the bare agent name (`owner = agent_name` only matches a
        # human-driven lease, e.g. `POST /nodes/{id}/start` without
        # `?agent=`) -- match both forms, or every runner-driven node
        # showed up as no agent's "current work" (final review Important
        # #6).
        current_row = db_mod.query_one(
            conn,
            "SELECT id, title, lease_until FROM nodes WHERE project_id = ? "
            "AND owner IN (?, 'runner:' || ?) "
            "AND status = 'in_progress' AND deleted_at IS NULL LIMIT 1",
            (project_id, name, name),
        )
        current = (
            {"node_id": current_row["id"], "title": current_row["title"], "lease_until": current_row["lease_until"]}
            if current_row is not None else None
        )
        spend = [
            {"unit": r["unit"], "spent": r["spent"]} for r in spend_rows if r["agent"] == name
        ]
        out.append({"agent": name, "roles": roles, "current": current, "spend": spend})
    return out
