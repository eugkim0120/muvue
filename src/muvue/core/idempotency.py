"""Request-id idempotency for every mutating verb (plan section 4: "All
mutating verbs accept `--request-id`; duplicates within 24 h are
no-ops").

`start`, `done`, `fail` and `ask` dedupe natively on their own events.
Every other verb goes through `once`: the first call with a given
`(verb, request_id)` runs and records a `request.<verb>.completed` event
carrying its result; a repeat within the 24h window returns that stored
result without running again.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

from . import db as db_mod
from . import events as events_mod


def _event_type(verb: str) -> str:
    return f"request.{verb}.completed"


def _to_jsonable(value: object) -> object:
    """Recursively convert `sqlite3.Row` to `dict` before JSON-encoding a
    verb's result for later replay. `core.projects.create_project` and
    `core.nodes.create_node` (among others) return `sqlite3.Row` objects;
    without this, `json.dumps(result, default=str)` stringified the whole
    row (`"<sqlite3.Row object at 0x...>"`) instead of its fields, so a
    retried request with the same `X-Request-Id` replayed garbage instead
    of the original result (final review Important #5)."""
    if isinstance(value, sqlite3.Row):
        return {k: _to_jsonable(v) for k, v in dict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _stored(conn: sqlite3.Connection, request_id: str, verb: str):
    prior = events_mod.find_recent_by_request_id(conn, request_id, _event_type(verb))
    if prior is None:
        return None
    return {"noop": True, "result": json.loads(prior["payload"])["result"]}


def _record(conn: sqlite3.Connection, request_id: str, verb: str, result, actor: str) -> None:
    events_mod.record_event(
        conn, project_id=None, node_id=None, actor=actor, actor_evidence=None,
        type_=_event_type(verb), request_id=request_id,
        payload={"verb": verb, "result": json.loads(json.dumps(_to_jsonable(result), default=str))},
    )


def once(
    conn: sqlite3.Connection,
    request_id: str | None,
    verb: str,
    fn: Callable[[], object],
    *,
    actor: str = "human",
    atomic: bool = True,
):
    """Run `fn` at most once per `(verb, request_id)` in the dedupe window.

    `atomic=True` checks, runs and records inside one `write_txn`, so two
    concurrent duplicates serialise. Verbs that do slow outside work (git,
    network, stopping processes) pass `atomic=False` so the write lock
    isn't held across it: check, run, record, with the verb's own
    idempotency covering the small race. A repeat returns
    `{"noop": True, "result": <first result>}`."""
    if not request_id:
        return fn()
    if atomic:
        with db_mod.write_txn(conn):
            stored = _stored(conn, request_id, verb)
            if stored is not None:
                return stored
            result = fn()
            _record(conn, request_id, verb, result, actor)
            return result
    with db_mod.read_txn(conn):
        stored = _stored(conn, request_id, verb)
    if stored is not None:
        return stored
    result = fn()
    with db_mod.write_txn(conn):
        _record(conn, request_id, verb, result, actor)
    return result
