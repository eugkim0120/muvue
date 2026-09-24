"""P6 acceptance #2: `merge --pr` generates a PR description body
containing the node's acceptance criteria and relevant decisions/notes."""

from __future__ import annotations

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, pr as pr_mod, projects


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def task(conn):
    project = projects.create_project(conn, goal="pr body test")
    project = projects.set_phase(conn, project["id"], "executing")
    return nodes.create_node(
        conn, project_id=project["id"], kind="task", title="add retry logic",
        body_md="handle rate limits gracefully",
        criteria=["retries on 429", "backs off exponentially"],
        criteria_mode="manual", status="ready",
    )


def test_pr_body_contains_criteria(conn, task):
    body = pr_mod.generate_pr_body(conn, task["id"])
    assert "retries on 429" in body
    assert "backs off exponentially" in body
    assert "add retry logic" in body


def test_pr_body_contains_relevant_decisions(conn, task):
    conn.execute(
        "INSERT INTO decisions (title, context, choice, status) "
        "VALUES ('retry strategy', 'rate limits', 'exponential backoff with jitter', 'current')"
    )
    conn.commit()
    body = pr_mod.generate_pr_body(conn, task["id"])
    assert "retry strategy" in body
    assert "exponential backoff with jitter" in body


def test_pr_body_contains_notes_and_external_refs(conn, task):
    nodes.add_note(conn, task["id"], kind="discovery", text="found the rate limit header name")
    conn.execute(
        "INSERT INTO external_refs (node_id, system, ext_id, url) "
        "VALUES (?, 'github', '42', 'https://github.com/o/r/issues/42')",
        (task["id"],),
    )
    conn.commit()
    body = pr_mod.generate_pr_body(conn, task["id"])
    assert "found the rate limit header name" in body
    assert "github#42" in body
    assert "https://github.com/o/r/issues/42" in body
