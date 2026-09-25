"""v4 section 4 "Brief": line-based wire format, `--budget N` (chars / 4),
`--since EVENT_ID` deltas, ranking (shared touches, then lexical), lessons
filtered to scope. Golden files at three budgets; regenerate with
`MUVUE_REGEN_GOLDEN=1 uv run pytest tests/test_brief_format.py`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import asks, brief, db as core_db, gates, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo

GOLDEN = Path(__file__).parent / "golden"


def _seed(conn) -> dict:
    config = MuvueConfig()
    project = projects.create_project(conn, goal="pricing rework")
    loader = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="Load raw prices",
        criteria=["reads v1 files"], criteria_mode="auto",
        predicted_touches=["pricing/raw.py"], status="pending",
    )
    target = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="Refactor pricing loader",
        body_md="Split parsing from file IO so the loader can read v2 price files.",
        criteria=["loader reads v2 files", "tests pass"], criteria_mode="auto",
        predicted_touches=["pricing/loader.py", "pricing/raw.py"], depends_on=[loader["id"]],
        status="pending",
    )
    other = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="Unrelated docs",
        criteria=["docs build"], criteria_mode="manual",
        predicted_touches=["docs/index.md"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    nodes.start(conn, target["id"], owner="agent-1")
    nodes.add_note(conn, target["id"], kind="decision", text="cache lives in the loader, not the caller", actor="agent")
    nodes.add_note(conn, loader["id"], kind="discovery", text="price files are latin-1 encoded, the loader must decode", actor="agent")
    nodes.add_note(conn, other["id"], kind="lesson", actor="agent", text=nodes.lesson_text(
        trigger="editing docs", failure="broke the build", do_instead="run mkdocs", scope="docs/*"))
    nodes.add_note(conn, loader["id"], kind="lesson", actor="agent", text=nodes.lesson_text(
        trigger="parsing prices", failure="floats lost cents", do_instead="use integer cents",
        scope="pricing/*"))
    asks.ask(conn, target["id"], question="keep the v1 reader?", default="yes")
    return {"project": project, "target": target, "loader": loader, "other": other}


@pytest.fixture
def seeded(tmp_path: Path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    ids = _seed(conn)
    yield conn, ids
    conn.close()


@pytest.mark.parametrize("budget", [60, 150, 2000])
def test_brief_matches_golden_at_three_budgets(seeded, budget):
    conn, ids = seeded
    result = brief.render_brief(conn, ids["target"]["id"], budget=budget)
    golden = GOLDEN / f"brief_budget_{budget}.txt"
    if os.environ.get("MUVUE_REGEN_GOLDEN"):
        golden.write_text(result["text"])
    assert result["text"] == golden.read_text()


@pytest.mark.parametrize("budget", [60, 150, 2000])
def test_brief_stays_within_budget_unless_required_lines_exceed_it(seeded, budget):
    conn, ids = seeded
    result = brief.render_brief(conn, ids["target"]["id"], budget=budget)
    required = result["text"].splitlines()[:4]
    assert result["tokens"] <= max(budget, brief.tokens("\n".join(required) + "\nomitted 99\n"))


def test_brief_line_format_and_ranking(seeded):
    conn, ids = seeded
    lines = brief.render_brief(conn, ids["target"]["id"], budget=2000)["text"].splitlines()
    t, dep = ids["target"]["id"], ids["loader"]["id"]
    assert lines[1] == (
        f'T{t} in_progress "Refactor pricing loader" deps:T{dep} '
        "touches:pricing/loader.py,pricing/raw.py tier:low"
    )
    kinds = [line.split(" ", 1)[0] for line in lines]
    # shared-touch nodes come before lexical matches
    assert kinds.index("related") < max(i for i, k in enumerate(kinds) if k == "note")
    related = [line for line in lines if line.startswith("related")]
    assert related == [f'related T{dep} ready "Load raw prices" shared:pricing/raw.py']


def test_lessons_are_filtered_to_scope(seeded):
    conn, ids = seeded
    text = brief.render_brief(conn, ids["target"]["id"], budget=2000)["text"]
    assert "integer cents" in text          # scope pricing/* overlaps the node's touches
    assert "mkdocs" not in text             # scope docs/* does not


def test_since_returns_only_newer_events(seeded):
    conn, ids = seeded
    first = brief.render_brief(conn, ids["target"]["id"])
    nodes.add_note(conn, ids["target"]["id"], kind="discovery", text="v2 files carry a BOM", actor="agent")
    delta = brief.render_brief(conn, ids["target"]["id"], since=first["cursor"])
    body = delta["text"].splitlines()[2:]
    assert len(body) == 1
    assert body[0].startswith("event E") and "note.added" in body[0] and "BOM" in body[0]
    assert "criteria" not in delta["text"]


def test_cli_brief_prints_lines_and_accepts_budget_and_since(tmp_path):
    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        ids = _seed(conn)
    finally:
        conn.close()
    node = str(ids["target"]["id"])
    out = subprocess.run(
        [sys.executable, "-m", "muvue", "brief", node, "--budget", "60", "--path", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith(f"muvue-brief v1 node:T{node}")
    cursor = out.stdout.split("cursor:E", 1)[1].split()[0]
    since = subprocess.run(
        [sys.executable, "-m", "muvue", "brief", node, "--since", cursor, "--path", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert since.returncode == 0, since.stderr
    assert "criteria" not in since.stdout
    structured = subprocess.run(
        [sys.executable, "-m", "muvue", "brief", node, "--json", "--path", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert json.loads(structured.stdout)["node"]["id"] == ids["target"]["id"]


def test_mcp_and_api_brief(tmp_path):
    from fastapi.testclient import TestClient

    from muvue.api import create_app
    from muvue.mcp_server import handle_request

    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        ids = _seed(conn)
    finally:
        conn.close()
    node_id = ids["target"]["id"]
    resp = handle_request(tmp_path, MuvueConfig(), {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "brief", "arguments": {"node_id": node_id, "budget": 60}},
    })
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["text"].startswith(f"muvue-brief v1 node:T{node_id}")

    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1")
    r = client.get("/brief", params={"node_id": node_id, "budget": 60})
    assert r.status_code == 200
    assert r.json()["text"].startswith(f"muvue-brief v1 node:T{node_id}")
    app = client.app
    headers = {"Authorization": f"Bearer {app.state.session.token}"}
    n = client.post(f"/nodes/{node_id}/note", json={"text": "found the BOM", "kind": "discovery"},
                    headers=headers)
    assert n.status_code == 200, n.text
    delta = client.get("/brief", params={"node_id": node_id, "since": r.json()["cursor"]})
    assert "found the BOM" in delta.json()["text"]
    s = client.get("/status")
    assert s.status_code == 200
    assert s.json()["counts"]["in_progress"] == 1
