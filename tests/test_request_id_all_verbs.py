"""Plan section 4: "All mutating verbs accept `--request-id`; duplicates
within 24 h are no-ops." Driven through the real CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import events, idempotency, nodes, projects
from muvue.core.repo_init import init_repo


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "muvue", *args, "--path", str(repo)],
                          cwd=repo, capture_output=True, text=True, timeout=60)


def _count(repo: Path, sql: str) -> int:
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


def test_once_runs_first_call_and_replays_the_result(tmp_path):
    conn = core_db.init_db(tmp_path / "m.db")
    calls = []
    try:
        first = idempotency.once(conn, "r1", "note", lambda: calls.append(1) or {"id": 7})
        again = idempotency.once(conn, "r1", "note", lambda: calls.append(1) or {"id": 8})
        other_verb = idempotency.once(conn, "r1", "ack", lambda: calls.append(1) or {"id": 9})
    finally:
        conn.close()
    assert first == {"id": 7}
    assert again == {"noop": True, "result": {"id": 7}}
    assert other_verb == {"id": 9}
    assert len(calls) == 2


def test_duplicate_project_create_makes_one_project(repo):
    a = _cli(repo, "project", "create", "--goal", "g", "--request-id", "p-1")
    b = _cli(repo, "project", "create", "--goal", "g", "--request-id", "p-1")
    assert a.returncode == 0 and b.returncode == 0, a.stderr + b.stderr
    assert json.loads(b.stdout)["noop"] is True
    assert _count(repo, "SELECT COUNT(*) FROM projects") == 1


def test_duplicate_note_and_decompose_and_spec(repo):
    pid = json.loads(_cli(repo, "project", "create", "--goal", "g").stdout)["id"]
    for _ in range(2):
        assert _cli(repo, "spec", str(pid), "--title", "s", "--body", "b",
                    "--request-id", "spec-1").returncode == 0
    assert _count(repo, "SELECT COUNT(*) FROM nodes WHERE kind = 'spec'") == 1
    spec_id = _count(repo, "SELECT id FROM nodes WHERE kind = 'spec'")
    assert _cli(repo, "approve", f"spec:{spec_id}", "--request-id", "ap-1").returncode == 0
    assert _cli(repo, "approve", f"spec:{spec_id}", "--request-id", "ap-1").returncode == 0
    for _ in range(2):
        assert _cli(repo, "decompose", str(spec_id), "--title", "t",
                    "--request-id", "dec-1").returncode == 0
    assert _count(repo, "SELECT COUNT(*) FROM nodes WHERE kind = 'task'") == 1
    task_id = _count(repo, "SELECT id FROM nodes WHERE kind = 'task'")
    for _ in range(2):
        out = _cli(repo, "note", str(task_id), "--kind", "discovery", "--text", "x",
                   "--request-id", "n-1")
        assert out.returncode == 0, out.stderr
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'note.added'") == 1


def test_duplicate_ack_and_pause_are_noops(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        p = projects.set_phase(c, projects.create_project(c, goal="g")["id"], "executing")
        eid = events.record_event(c, project_id=None, node_id=None, actor="hook",
                                  type_="inbox.unattributed_commit", payload={})
    finally:
        c.close()
    for _ in range(2):
        assert _cli(repo, "ack", str(eid), "--request-id", "ack-1").returncode == 0
        assert _cli(repo, "pause", str(p["id"]), "--request-id", "pz-1").returncode == 0
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'event.acked'") == 1
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'project.phase_changed' "
                        "AND payload LIKE '%paused%'") == 1


@pytest.mark.parametrize("verb", [
    "project create", "spec", "decompose", "replan", "note", "approve", "reject", "answer",
    "ack", "merge", "close", "handoff", "import", "pause", "resume", "propose-revision",
])
def test_every_mutating_cli_verb_documents_request_id(verb):
    out = subprocess.run([sys.executable, "-m", "muvue", *verb.split(), "--help"],
                         capture_output=True, text=True)
    assert "--request-id" in out.stdout, verb


def _ready_task(repo: Path) -> tuple[int, int]:
    from muvue.core import gates
    from muvue.core.config import MuvueConfig

    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        project = projects.create_project(conn, goal="g")
        task = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="t",
            criteria=["passes"], criteria_mode="auto", status="pending",
        )
        gates.approve_gate2(conn, project["id"], config=MuvueConfig())
        return project["id"], task["id"]
    finally:
        conn.close()


def test_mcp_note_and_replan_dedupe_on_request_id(repo):
    from muvue.core.config import MuvueConfig
    from muvue.mcp_server import handle_request

    _, task_id = _ready_task(repo)
    for _ in range(2):
        resp = handle_request(repo, MuvueConfig(), {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "note", "arguments": {
                "node_id": task_id, "text": "found it", "request_id": "mcp-n-1"}},
        })
        assert "result" in resp, resp
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'note.added'") == 1


def test_api_mutations_dedupe_on_x_request_id_header(repo):
    from fastapi.testclient import TestClient

    from muvue.api import create_app

    project_id, task_id = _ready_task(repo)
    app = create_app(repo)
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Authorization": f"Bearer {app.state.session.token}", "X-Request-Id": "c-1"}
    for _ in range(2):
        r = client.post(f"/nodes/{task_id}/comment", json={"text": "tighten this"}, headers=headers)
        assert r.status_code == 200, r.text
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'note.added'") == 1

    headers["X-Request-Id"] = "pause-1"
    first = client.post(f"/projects/{project_id}/pause", headers=headers)
    second = client.post(f"/projects/{project_id}/pause", headers=headers)
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()["noop"] is True
    assert _count(repo, "SELECT COUNT(*) FROM events WHERE type = 'project.phase_changed' AND payload LIKE '%paused%'") == 1
