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


def test_daemon_drains_the_hook_queue_with_no_dashboard_connected(repo):
    """v4 section 4a: "the daemon drains continuously". The drain runs in
    a background task for the daemon's whole lifetime -- it used to ride
    the SSE loop, so with no dashboard open nothing drained."""
    import time

    from fastapi.testclient import TestClient

    queue_path = repo / ".muvue" / "queue.jsonl"
    queue_path.write_text('{"event": "stop", "ts": "t0", "node_id": null}\n')

    app = create_app(repo, config=MuvueConfig(), drain_interval_s=0.05)
    with TestClient(app, base_url="http://127.0.0.1"):
        deadline = time.monotonic() + 5
        row = None
        while row is None and time.monotonic() < deadline:
            conn = core_db.connect(repo / ".muvue" / "muvue.db")
            row = conn.execute("SELECT type FROM events WHERE type = 'hook.stop'").fetchone()
            conn.close()
            time.sleep(0.05)
    assert row is not None
    assert not queue_path.exists() or queue_path.read_text().strip() == ""
