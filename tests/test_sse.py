"""P2: SSE endpoint polls `PRAGMA data_version` and pushes a message when
the DB changes (plan section 8: "SSE (polls PRAGMA data_version)").

Exercises the real registered `/events/stream` route function directly
rather than through a full ASGI client transport: `httpx.AsyncClient` +
`ASGITransport` streaming a still-open SSE response deadlocks in this
sandbox (Starlette's disconnect-listener task never gets scheduled against
the test transport -- reproduced independently of muvue's own code with a
minimal FastAPI app; see docs/decisions.md). Calling the endpoint function
and iterating its `StreamingResponse.body_iterator` still runs the exact
production code path (route -> generator -> `PRAGMA data_version` poll),
just without the ASGI request/response framing around it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from muvue.api.app import create_app
from muvue.core import db as core_db, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


def _stream_endpoint(app):
    for route in app.routes:
        if getattr(route, "path", None) == "/events/stream":
            return route.endpoint
    raise AssertionError("/events/stream route not registered")


def test_sse_emits_data_version_message(repo):
    app = create_app(repo, config=MuvueConfig())
    endpoint = _stream_endpoint(app)

    async def run():
        response = await endpoint()
        chunk = await response.body_iterator.__anext__()
        return chunk

    chunk = asyncio.run(run())
    assert chunk.startswith("data: ")
    payload = json.loads(chunk[len("data: "):].strip())
    assert "data_version" in payload


def test_sse_emits_a_second_message_after_a_mutation(repo):
    app = create_app(repo, config=MuvueConfig())
    endpoint = _stream_endpoint(app)

    async def run():
        response = await endpoint()
        agen = response.body_iterator
        first = await agen.__anext__()

        conn = core_db.connect(repo / ".muvue" / "muvue.db")
        projects.create_project(conn, goal="triggers a data_version bump")
        conn.close()

        second = await agen.__anext__()
        return first, second

    first, second = asyncio.run(run())
    v1 = json.loads(first[len("data: "):].strip())["data_version"]
    v2 = json.loads(second[len("data: "):].strip())["data_version"]
    assert v2 > v1


def test_sse_loop_drains_the_hook_fast_path_queue(repo):
    """v4 section 4a: "the daemon drains continuously" -- this SSE loop
    is the daemon's one continuous, restart-safe loop today (P2a's
    dedicated daemon process doesn't exist yet), so it rides the
    bounded drain as a periodic task. A spooled queue line with no
    external write should still get processed (and its `hook.<event>`
    audit event bump `PRAGMA data_version`) purely from the loop's own
    periodic drain call, with no test-injected mutation in between."""
    queue_path = repo / ".muvue" / "queue.jsonl"
    queue_path.write_text('{"event": "stop", "ts": "t0", "node_id": null}\n')

    app = create_app(repo, config=MuvueConfig())
    endpoint = _stream_endpoint(app)

    async def run():
        response = await endpoint()
        agen = response.body_iterator
        first = await agen.__anext__()  # initial data_version snapshot
        second = await agen.__anext__()  # after >=1 drain-call iteration
        return first, second

    first, second = asyncio.run(run())
    v1 = json.loads(first[len("data: "):].strip())["data_version"]
    v2 = json.loads(second[len("data: "):].strip())["data_version"]
    assert v2 > v1
    assert not queue_path.exists() or queue_path.read_text().strip() == ""

    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    row = conn.execute("SELECT type FROM events WHERE type = 'hook.stop'").fetchone()
    conn.close()
    assert row is not None
