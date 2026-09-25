"""`merge --pr` body generation (plan section 4 human verb "merge
[--pr]", section 6 "Merging", P6): a PR description body -- markdown --
assembled from a node's own acceptance criteria plus any decisions/notes/
linked issues relevant to it. No real `gh pr create` call: no network
access in this environment (plan working rule 2), so this returns the
body as text (CLI stdout / an API response field) for a human to paste,
or for a future real API-call site to send verbatim -- see
docs/decisions.md.
"""

from __future__ import annotations

import json
import sqlite3

from . import db as db_mod
from . import queries as queries_mod


def generate_pr_body(conn: sqlite3.Connection, node_id: int) -> str:
    """Independent of whether the node actually merged cleanly (plan
    section 6's conflict path still produces a node worth describing) --
    this only reads the node's own data plus structure-aware decisions
    found the same way `brief` finds them (`core.queries.search_decisions`,
    P6)."""
    detail = queries_mod.show_node(conn, node_id)
    node = detail["node"]
    criteria = json.loads(node["criteria_json"] or "[]")
    notes = detail["notes"]
    query_text = f"{node['title']} {node['body_md']}"
    relevant_decisions = queries_mod.search_decisions(conn, query_text)
    external_refs = [
        dict(r) for r in db_mod.query_all(
            conn,
            "SELECT * FROM external_refs WHERE node_id = ?",
            (node_id,),
        )
    ]

    lines = [f"# {node['title']}", ""]
    if node["body_md"]:
        lines += [node["body_md"], ""]

    lines.append("## Acceptance criteria")
    lines += [f"- {c}" for c in criteria] if criteria else ["_none recorded_"]
    lines.append("")

    lines.append("## Decisions")
    if relevant_decisions:
        for d in relevant_decisions:
            lines.append(f"- **{d['title']}**: {d.get('choice') or ''}")
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Notes")
    if notes:
        for n in notes:
            lines.append(f"- [{n['kind']}] {n['text']}")
    else:
        lines.append("_none_")
    lines.append("")

    if external_refs:
        lines.append("## Linked issues")
        for r in external_refs:
            lines.append(f"- {r['system']}#{r['ext_id']}: {r['url']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
