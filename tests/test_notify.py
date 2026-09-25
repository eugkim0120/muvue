"""`[notify] url` (v4 section 2, "ntfy topic or webhook"; re-judges
decision #97): inbox-worthy events are POSTed as text lines, at most
once, from the runner and the daemon."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from muvue.core import asks, db as core_db, gates, nodes, notify, projects
from muvue.core.config import MuvueConfig, NotifyConfig


class _Sink(http.server.BaseHTTPRequestHandler):
    received: list[tuple[dict, str]] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        _Sink.received.append((dict(self.headers), self.rfile.read(length).decode()))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def sink():
    _Sink.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/topic", _Sink.received
    server.shutdown()


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


def _in_progress(conn):
    project = projects.create_project(conn, goal="n")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", criteria=["ok"],
        criteria_mode="auto", status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=MuvueConfig())
    nodes.start(conn, task["id"], owner="agent-1")
    return task


def test_no_url_sends_nothing(conn):
    _in_progress(conn)
    assert notify.flush(conn, MuvueConfig()) == {"sent": 0}


def test_first_flush_starts_at_the_end_then_new_items_are_posted_once(conn, sink):
    url, received = sink
    config = MuvueConfig(notify=NotifyConfig(url=url))
    task = _in_progress(conn)
    asks.ask(conn, task["id"], question="old question?", default="yes")
    assert notify.flush(conn, config) == {"sent": 0}  # history is not replayed
    asks.ask(conn, task["id"], question="keep the v1 reader?", default="yes")
    nodes.block(conn, task["id"], reason="question", actor="agent-1")
    assert notify.flush(conn, config) == {"sent": 2}
    headers, body = received[0]
    assert body.splitlines() == [
        f'T{task["id"]} question.asked "keep the v1 reader?"',
        f"T{task['id']} node.blocked (question)",
    ]
    assert headers["Content-Type"].startswith("text/plain")
    assert notify.flush(conn, config) == {"sent": 0}  # at most once
    assert len(received) == 1


def test_an_unreachable_url_is_recorded_and_not_retried(conn):
    config = MuvueConfig(notify=NotifyConfig(url="http://127.0.0.1:9/unreachable"))
    task = _in_progress(conn)
    notify.flush(conn, config)
    asks.ask(conn, task["id"], question="q?", default="d")
    result = notify.flush(conn, config)
    assert result["sent"] == 0 and "error" in result
    failed = core_db.query_all(conn, "SELECT * FROM events WHERE type = 'notify.failed'")
    assert len(failed) == 1
    assert notify.flush(conn, config) == {"sent": 0}


def test_idle_flushes_do_not_write_events(conn, sink):
    url, _ = sink
    config = MuvueConfig(notify=NotifyConfig(url=url))
    _in_progress(conn)
    notify.flush(conn, config)
    before = core_db.query_one(conn, "SELECT COUNT(*) c FROM events")["c"]
    for _ in range(5):
        notify.flush(conn, config)
    assert core_db.query_one(conn, "SELECT COUNT(*) c FROM events")["c"] == before


def test_runner_escalation_is_posted(tmp_path, sink):
    """The rate-limit timeout escalation reaches the URL (supersedes the
    event-only fallback in decision #97)."""
    import shutil
    from datetime import datetime, timezone

    from muvue.core import runner
    from muvue.core.config import AgentConfig, ChecksConfig, RoutingConfig

    if shutil.which("muvue-fake-agent") is None:
        pytest.skip("fake agent not installed")
    url, received = sink
    db_path = tmp_path / "muvue.db"
    c = core_db.init_db(db_path)
    project = projects.create_project(c, goal="n")
    projects.set_phase(c, project["id"], "executing")
    nodes.create_node(c, project_id=project["id"], kind="task", title="t", status="ready")
    config = MuvueConfig(
        notify=NotifyConfig(url=url), checks=ChecksConfig(test="true", lint="true"),
        agents={"fake": AgentConfig(
            command="MUVUE_FAKE_BEHAVIOR=rate_limited MUVUE_FAKE_RETRY_AFTER_SECONDS=3600 muvue-fake-agent",
            usage_parser="fake", cost_model="tokens", on_rate_limit="wait", max_wait_minutes=1)},
        routing=RoutingConfig(spec="fake", task="fake", subtask="fake"),
    )
    notify.flush(c, config)
    c.close()
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def sleep(s):
        from datetime import timedelta
        clock["now"] += timedelta(seconds=s)

    runner.run(db_path, config, tmp_path, now_fn=lambda: clock["now"], sleep_fn=sleep)
    bodies = "".join(body for _, body in received)
    assert "runner.rate_limit_wait_exhausted" in bodies
    assert "node.blocked (rate_limit)" in bodies
