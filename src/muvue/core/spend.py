"""Per-driver budget accounting (v4 section 2/3/6): "Budget is per
driver, in that driver's unit. No cross-unit arithmetic." `agent_spend`
holds one row per (project, agent, unit); this module is the minimal
write/read helper for it.

Out of scope this phase (separate follow-up, per the v4 handoff plan's
own P5 budget-enforcement work): actually checking a driver's spend
against its `[agents.<x>.budget]` limit, stopping the offending driver
at 100%/warning at 80%, or wiring this into `core.runner`'s scheduling
loop. This module only accumulates and reads spend -- see
docs/decisions.md.
"""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import events as events_mod


def record_spend(
    conn: sqlite3.Connection,
    project_id: int,
    agent: str,
    unit: str,
    amount: float,
    *,
    actor: str = "daemon",
    actor_evidence: str = "subprocess",
) -> sqlite3.Row:
    """Increment `agent_spend[project_id, agent, unit].spent` by
    `amount` (upserting the row if it doesn't exist yet). Called
    alongside `core.runner._record_usage`'s `node_usage` insert -- see
    that call site -- once per driver invocation, in whatever unit that
    driver's `cost_model` produces (usd/tokens/requests)."""
    with db_mod.write_txn(conn):
        conn.execute(
            "INSERT INTO agent_spend (project_id, agent, unit, spent) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (project_id, agent, unit) DO UPDATE SET spent = spent + excluded.spent",
            (project_id, agent, unit, amount),
        )
        row = conn.execute(
            "SELECT * FROM agent_spend WHERE project_id = ? AND agent = ? AND unit = ?",
            (project_id, agent, unit),
        ).fetchone()
        events_mod.record_event(
            conn,
            project_id=project_id,
            node_id=None,
            actor=actor,
            actor_evidence=actor_evidence,
            type_="spend.recorded",
            payload={"project_id": project_id, "agent": agent, "unit": unit, "amount": amount},
        )
        return row


def get_spend(conn: sqlite3.Connection, project_id: int, agent: str, unit: str) -> float:
    row = conn.execute(
        "SELECT spent FROM agent_spend WHERE project_id = ? AND agent = ? AND unit = ?",
        (project_id, agent, unit),
    ).fetchone()
    return row["spent"] if row is not None else 0.0


def project_spend(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    """Every (agent, unit, spent) row for a project -- the dashboard's
    "spend vs budget per driver" KPI (plan section 8) reads this."""
    return conn.execute(
        "SELECT agent, unit, spent FROM agent_spend WHERE project_id = ? ORDER BY agent, unit",
        (project_id,),
    ).fetchall()
