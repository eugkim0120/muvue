"""P6: `import --from github#N` plumbing (plan section 4/11). No real
GitHub API access here -- issue data is injected (dict, local file, or a
swappable `fetch_fn` seam), and the linking logic (`external_refs`) is
real and tested."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import imports as imports_mod
from muvue.core import nodes, projects


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def task(conn):
    project = projects.create_project(conn, goal="import test")
    project = projects.set_phase(conn, project["id"], "executing")
    return nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")


def test_import_github_issue_with_inline_data(conn, task):
    result = imports_mod.import_github_issue(
        conn, task["id"], 123,
        data={"number": 123, "title": "fix the thing", "url": "https://github.com/o/r/issues/123",
              "body": "steps to repro"},
    )
    ref = result["external_ref"]
    assert ref["system"] == "github"
    assert ref["ext_id"] == "123"
    assert ref["url"] == "https://github.com/o/r/issues/123"

    row = conn.execute(
        "SELECT * FROM external_refs WHERE node_id = ?", (task["id"],)
    ).fetchone()
    assert row["ext_id"] == "123"


def test_import_github_issue_from_file(conn, task, tmp_path):
    data_path = tmp_path / "issue.json"
    data_path.write_text(json.dumps({"number": 7, "title": "t", "url": "https://x/7"}))
    result = imports_mod.import_github_issue(conn, task["id"], 7, data_path=data_path)
    assert result["external_ref"]["ext_id"] == "7"


def test_import_github_issue_with_fetch_fn(conn, task):
    calls = []

    def fetch(number):
        calls.append(number)
        return {"number": number, "title": "fetched", "url": f"https://x/{number}"}

    result = imports_mod.import_github_issue(conn, task["id"], 9, fetch_fn=fetch)
    assert calls == [9]
    assert result["external_ref"]["ext_id"] == "9"


def test_import_github_refuses_non_human(conn, task):
    with pytest.raises(imports_mod.HumanOnly):
        imports_mod.import_github_issue(
            conn, task["id"], 1, data={"number": 1}, actor="agent",
        )


def test_import_github_needs_some_data_source(conn, task):
    with pytest.raises(imports_mod.ImportError_):
        imports_mod.import_github_issue(conn, task["id"], 1)
