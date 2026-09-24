"""Decision logic behind the Claude Code adapter's hooks (plan section 7):

- SessionStart -> `brief`
- PreToolUse -> block Edit/Write with no `in_progress` node, or a node
  that's `awaiting_approval`.
- PreCompact -> require a summary first.
- Stop -> block ending the turn with unlogged `in_progress` work.

v4 section 7 / changelog item 10 (REMOVED, not fixed): `PreToolUse` used
to also string-match a `Bash` tool call's command for something
resembling `git commit` and block it for a missing `Muvue-Node:`/
`Refs:` trailer. That match is defeated by `git -C`, heredocs, chained
commands, aliases and scripts, and produces false positives on any
string merely containing "git commit" (e.g. a commit message, or a
comment). v4's position: string-matching a shell command is
fundamentally unsound as an enforcement mechanism, not something a
better regex fixes. The real enforcement moved post-hoc, to
`core.hooks.handle_post_commit` / `core.drift.
flag_general_unattributed_commit` -- see docs/decisions.md #100 and
tests/test_trailer_enforcement_relocation.py. Strict mode's real
barrier remains `pre-receive` (`core.strict`), untouched by this
change.

Every function here is pure with respect to Claude Code's own hook
transport (stdin JSON in, decision dict out) -- `cli/main.py`'s `hook`
command owns reading stdin, resolving `node_id` (explicit in the
payload, or `core.adapters.get_current_node`), and mapping the returned
decision to an exit code. Claude Code's hook JSON contract (`"decision":
"block"`/`"allow"`, `hookSpecificOutput`) is reproduced here best-effort
from memory of the documented shape, not verified against a live Claude
Code install -- see docs/providers.md and docs/decisions.md.
"""

from __future__ import annotations

import sqlite3

from . import nodes as nodes_mod
from . import queries


def session_start(conn: sqlite3.Connection, *, node_id: int | None) -> dict:
    if node_id is None:
        return {"decision": "allow"}
    try:
        brief = queries.brief_node(conn, node_id)
    except LookupError:
        return {"decision": "allow"}
    return {"decision": "allow", "hookSpecificOutput": {"additionalContext": brief}}


def pre_tool_use(
    conn: sqlite3.Connection, *, tool_name: str, tool_input: dict | None, node_id: int | None
) -> dict:
    tool_input = tool_input or {}
    if tool_name in ("Edit", "Write"):
        if node_id is None:
            return {
                "decision": "block",
                "reason": "no active muvue node -- run `muvue start NODE_ID --owner ...` first",
            }
        try:
            node = nodes_mod.get_node(conn, node_id)
        except LookupError:
            return {"decision": "block", "reason": f"no such muvue node: {node_id}"}
        if node["status"] == "awaiting_approval":
            return {"decision": "block", "reason": f"node {node_id} is awaiting_approval"}
        if node["status"] != "in_progress":
            return {
                "decision": "block",
                "reason": f"node {node_id} is {node['status']!r}, not in_progress",
            }
    return {"decision": "allow"}


def pre_compact(conn: sqlite3.Connection, *, node_id: int | None, summary: str | None) -> dict:
    if node_id is None:
        return {"decision": "allow"}
    try:
        node = nodes_mod.get_node(conn, node_id)
    except LookupError:
        return {"decision": "allow"}
    if node["status"] != "in_progress":
        return {"decision": "allow"}
    if not summary:
        return {
            "decision": "block",
            "reason": "PreCompact requires a summary before compacting (plan section 7)",
        }
    return {"decision": "allow"}


def stop(conn: sqlite3.Connection, *, node_id: int | None) -> dict:
    if node_id is None:
        return {"decision": "allow"}
    try:
        node = nodes_mod.get_node(conn, node_id)
    except LookupError:
        return {"decision": "allow"}
    if node["status"] != "in_progress":
        return {"decision": "allow"}
    count = conn.execute(
        "SELECT COUNT(*) c FROM notes WHERE node_id = ?", (node_id,)
    ).fetchone()["c"]
    if count == 0:
        return {
            "decision": "block",
            "reason": f"node {node_id} is in_progress with no notes logged -- "
            "`muvue note` before ending the turn",
        }
    return {"decision": "allow"}
