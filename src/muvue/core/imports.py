"""`import --from github#N` (plan section 4, human verb; section 11,
P6). No live network access in this environment and no new HTTP-client
dependency (plan working rule 2) -- the real GitHub fetch is a
documented, thin, swappable seam (`fetch_fn`) rather than implemented
against the live API. Issue data is supplied locally instead, one of
three ways: an inline dict, a local JSON file (`--data
path/to/mock_issue.json` at the CLI), or an injected `fetch_fn(number) ->
dict` (what real GitHub-API wiring plugs into later -- see
docs/decisions.md). The linking logic itself (`external_refs`) is real
and fully tested regardless of where the data came from.

Expected issue dict shape (a minimal subset of GitHub's own Issue/PR
resource): `{"number": int, "title": str, "url": str, "body": str}`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from . import events as events_mod
from . import nodes as nodes_mod


class ImportError_(Exception):
    """Named with a trailing underscore to avoid shadowing the builtin
    `ImportError` (this module is `muvue.core.imports`, and `import` is
    itself a keyword, hence `imports.py`/`imports_mod` throughout)."""


class HumanOnly(ImportError_):
    """`import` is a human verb (plan section 4: "Never exposed over
    MCP"), enforced in core itself -- same duplicated-per-module pattern
    as `core.nodes.HumanOnly` / `core.gates.HumanOnly` / `core.close.HumanOnly`."""


def _require_human(actor: str) -> None:
    if actor != "human":
        raise HumanOnly(
            f"only a human may perform this action (actor was {actor!r}); "
            "human verbs are never exposed over MCP (plan section 4)"
        )


def import_github_issue(
    conn: sqlite3.Connection,
    node_id: int,
    issue_number: int,
    *,
    data: dict | None = None,
    data_path: str | Path | None = None,
    fetch_fn: Callable[[int], dict] | None = None,
    actor: str = "human",
) -> dict:
    """Link `node_id` to `github#<issue_number>` in `external_refs`.
    Exactly one data source is needed -- `data` (inline), `data_path` (a
    local JSON file), or `fetch_fn` (called as `fetch_fn(issue_number)`;
    a real GitHub-API-backed implementation is future work, not built
    here). Raises `ImportError_` if none is given."""
    _require_human(actor)
    node = nodes_mod.get_node(conn, node_id)

    if data is None and data_path is not None:
        data = json.loads(Path(data_path).read_text())
    if data is None and fetch_fn is not None:
        data = fetch_fn(issue_number)
    if data is None:
        raise ImportError_(
            "import_github_issue needs one of data=/data_path=/fetch_fn= "
            "(no live GitHub API access in this environment -- plan working rule 2)"
        )

    url = data.get("url") or f"https://github.com/issues/{issue_number}"
    cur = conn.execute(
        "INSERT INTO external_refs (node_id, system, ext_id, url) VALUES (?, 'github', ?, ?)",
        (node_id, str(issue_number), url),
    )
    row = conn.execute("SELECT * FROM external_refs WHERE id = ?", (cur.lastrowid,)).fetchone()
    events_mod.record_event(
        conn, project_id=node["project_id"], node_id=node_id, actor=actor,
        type_="external_ref.added",
        payload={**dict(row), "issue_title": data.get("title"), "issue_body": data.get("body")},
    )
    conn.commit()
    return {"external_ref": dict(row), "issue": data}
