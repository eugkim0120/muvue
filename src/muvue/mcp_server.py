"""Minimal MCP stdio server (plan section 4/7): exposes *agent* verbs
only -- `brief/show/start/done/fail/note/ask/wait/replan/status` --
never the human verbs (`approve/reject/ack/merge/close/pause/resume/
handoff/import`), per plan section 4's explicit split: "Never exposed
over MCP" for human verbs. `core.gates`/`core.nodes` also refuse a
non-human actor at the human verbs themselves (see `HumanOnly`), so this
is belt-and-suspenders, not the only enforcement layer.

No MCP SDK dependency: pyproject.toml (plan working rule 2, "no
dependencies beyond stack") ships none, and the wire protocol MCP's
stdio transport uses is simple enough to hand-roll correctly -- one
JSON-RPC 2.0 message per line on stdin, one per line on stdout (no
Content-Length framing, unlike LSP). `handle_request` is pure
request-in/response-out (or `None` for a notification) so it's testable
without a real stdio subprocess; `run_stdio` is the thin I/O loop.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Callable

from . import core

PROTOCOL_VERSION_MCP = "2024-11-05"  # the MCP wire-protocol version this server speaks


def _brief(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.queries.brief_node(conn, args["node_id"])


def _show(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.queries.show_node(conn, args["node_id"])


def _start(conn: sqlite3.Connection, config, args: dict, repo_root: Path | None = None) -> dict:
    return core.nodes.start(
        conn, args["node_id"], owner=args["owner"], request_id=args.get("request_id"),
        lease_minutes=config.planning.lease_minutes, actor_evidence="mcp",
        config=config, repo_root=repo_root,
    )


def _done(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.nodes.done(
        conn, args["node_id"], owner=args["owner"], summary=args.get("summary"),
        request_id=args.get("request_id"), config=config,
        expected_version=args.get("version"), actor_evidence="mcp",
    )


def _fail(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.nodes.fail(
        conn, args["node_id"], owner=args["owner"], lesson=args["lesson"],
        trigger=args["trigger"], do_instead=args["do_instead"],
        scope=args["scope"], request_id=args.get("request_id"),
        expected_version=args.get("version"), actor_evidence="mcp",
    )


def _note(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.idempotency.once(
        conn, args.get("request_id"), "note",
        lambda: core.nodes.add_note(
            conn, args["node_id"], kind=args.get("kind", "discovery"), text=args["text"],
            actor="agent", actor_evidence="mcp", pinned=args.get("pinned", False),
        ),
        actor="agent",
    )


def _ask(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.asks.ask(
        conn, args["node_id"], question=args["question"], default=args["default"],
        default_ok=args.get("default_ok", False),
        request_id=args.get("request_id"), actor_evidence="mcp",
    )


def _wait(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.asks.wait(
        conn, args["question_id"], timeout_minutes=config.planning.ask_timeout_minutes,
        default_ok=args.get("default_ok", False), actor_evidence="mcp",
    )


def _replan(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.idempotency.once(
        conn, args.get("request_id"), "replan",
        lambda: core.revisions.replan_add_subtask(
            conn, parent_task_id=args["parent_task_id"], title=args["title"],
            body_md=args.get("body_md", ""), actor_evidence="mcp",
        ),
        actor="agent",
    )


def _status(conn: sqlite3.Connection, config, args: dict) -> dict:
    return core.queries.status_summary(conn, args.get("project_id"))


# name -> (handler, JSON schema for its arguments, one-line description)
TOOLS: dict[str, tuple[Callable, dict, str]] = {
    "brief": (_brief, {"type": "object", "properties": {"node_id": {"type": "integer"}}, "required": ["node_id"]}, "What an agent needs to start work on a node."),
    "show": (_show, {"type": "object", "properties": {"node_id": {"type": "integer"}}, "required": ["node_id"]}, "Full detail for one node."),
    "start": (_start, {"type": "object", "properties": {"node_id": {"type": "integer"}, "owner": {"type": "string"}, "request_id": {"type": "string"}}, "required": ["node_id", "owner"]}, "Take the lease on a ready node."),
    "done": (_done, {"type": "object", "properties": {"node_id": {"type": "integer"}, "owner": {"type": "string"}, "summary": {"type": "string"}, "version": {"type": "integer"}, "request_id": {"type": "string"}}, "required": ["node_id", "owner"]}, "Mark a node done (may stop at review)."),
    "fail": (_fail, {"type": "object", "properties": {"node_id": {"type": "integer"}, "owner": {"type": "string"}, "lesson": {"type": "string"}, "trigger": {"type": "string"}, "do_instead": {"type": "string"}, "scope": {"type": "string"}, "version": {"type": "integer"}, "request_id": {"type": "string"}}, "required": ["node_id", "owner", "lesson", "trigger", "do_instead", "scope"]}, "Fail a node, recording a lesson (trigger, failure, do_instead, scope)."),
    "note": (_note, {"type": "object", "properties": {"node_id": {"type": "integer"}, "text": {"type": "string"}, "kind": {"type": "string"}, "pinned": {"type": "boolean"}, "request_id": {"type": "string"}}, "required": ["node_id", "text"]}, "Record a discovery/decision/lesson note."),
    "ask": (_ask, {"type": "object", "properties": {"node_id": {"type": "integer"}, "question": {"type": "string"}, "default": {"type": "string"}, "default_ok": {"type": "boolean"}, "request_id": {"type": "string"}}, "required": ["node_id", "question", "default"]}, "Ask a human a question with a proposed default."),
    "wait": (_wait, {"type": "object", "properties": {"question_id": {"type": "integer"}, "default_ok": {"type": "boolean"}}, "required": ["question_id"]}, "Poll a question once."),
    "replan": (_replan, {"type": "object", "properties": {"parent_task_id": {"type": "integer"}, "title": {"type": "string"}, "body_md": {"type": "string"}, "request_id": {"type": "string"}}, "required": ["parent_task_id", "title"]}, "Add a subtask within an already-approved task's scope."),
    "status": (_status, {"type": "object", "properties": {"project_id": {"type": "integer"}}}, "Node counts by status."),
}


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tools_list_payload() -> dict:
    return {
        "tools": [
            {"name": name, "description": desc, "inputSchema": schema}
            for name, (_, schema, desc) in TOOLS.items()
        ]
    }


def _call_tool(repo_root: Path, config, name: str, arguments: dict) -> dict:
    entry = TOOLS.get(name)
    if entry is None:
        raise McpError(-32601, f"unknown tool: {name!r}")
    handler, _, _ = entry
    conn = core.db.connect(repo_root / ".muvue" / "muvue.db")
    try:
        try:
            # `start` alone also needs repo_root (v4 section 5, branch
            # coherence -- "every start" -- and, pre-existing, strict-mode
            # worktree binding; every other verb's core.* call takes
            # (conn, ...) with no repo_root parameter at all).
            if name == "start":
                result = handler(conn, config, arguments, repo_root)
            else:
                result = handler(conn, config, arguments)
        except KeyError as e:
            raise McpError(-32602, f"missing required argument: {e}") from e
        except (
            core.nodes.NodeError,
            core.gates.GateError,
            core.asks.AskError,
            core.state_machine.InvalidTransition,
            core.state_machine.NotLeaseOwner,
            LookupError,
        ) as e:
            raise McpError(-32000, str(e)) from e
    finally:
        conn.close()
    return result


def handle_request(repo_root: Path, config, request: dict) -> dict | None:
    """Process one decoded JSON-RPC 2.0 message. Returns the response
    object, or None for a notification (no `id` -- no response expected)."""
    req_id = request.get("id")
    is_notification = "id" not in request
    method = request.get("method")

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION_MCP,
                "serverInfo": {"name": "muvue", "version": str(config.protocol_version)},
                "capabilities": {"tools": {}},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            result = _tools_list_payload()
        elif method == "tools/call":
            params = request.get("params", {})
            tool_result = _call_tool(repo_root, config, params.get("name"), params.get("arguments", {}) or {})
            result = {"content": [{"type": "text", "text": json.dumps(tool_result, default=str)}]}
        elif method == "shutdown":
            result = {}
        else:
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except McpError as e:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": e.code, "message": e.message}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def run_stdio(repo_root: Path, config=None) -> None:
    """Blocking stdio loop: one JSON-RPC message per line in, one per
    line out. `muvue mcp` is the CLI entry point that calls this."""
    config = config or core.load_config(repo_root / ".muvue" / "config.toml")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(repo_root, config, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()
