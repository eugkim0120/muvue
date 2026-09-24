"""P2 acceptance #2: expired leases revert to ready (+attempts) on
reconcile-on-start. Also covers queue draining and session tokens."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from muvue.core import daemon, db as core_db, nodes, projects, rebuild


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    p = projects.create_project(conn, goal="daemon test")
    return projects.set_phase(conn, p["id"], "executing")


def test_reconcile_reverts_expired_lease_to_ready_with_attempts_incremented(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=3,
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    assert nodes.get_node(conn, task["id"])["status"] == "in_progress"

    reverted = daemon.reconcile_leases(conn)
    assert len(reverted) == 1
    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "ready"
    assert row["owner"] is None
    assert row["lease_until"] is None
    assert row["attempts"] == 1


def test_reconcile_moves_to_failed_once_max_attempts_exhausted(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=1,
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    daemon.reconcile_leases(conn)
    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "failed"


def test_reconcile_leaves_unexpired_leases_alone(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=60)
    reverted = daemon.reconcile_leases(conn)
    assert reverted == []
    assert nodes.get_node(conn, task["id"])["status"] == "in_progress"


def test_reconcile_is_idempotent_and_does_not_double_touch(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    daemon.reconcile_leases(conn)
    second = daemon.reconcile_leases(conn)
    assert second == []  # already ready, not in_progress anymore


def test_reconcile_on_start_reverts_and_drains_queue(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    result = daemon.reconcile_on_start(conn)
    assert len(result["reverted_nodes"]) == 1
    assert result["drained_events"] > 0
    # a second pass finds nothing left unacked / expired
    result2 = daemon.reconcile_on_start(conn)
    assert result2["reverted_nodes"] == []
    assert result2["drained_events"] == 0


def test_process_queue_marks_events_acked(conn, project):
    nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    unacked_before = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE acked_at IS NULL"
    ).fetchone()["c"]
    assert unacked_before > 0
    drained = daemon.process_queue(conn)
    assert drained == unacked_before
    unacked_after = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE acked_at IS NULL"
    ).fetchone()["c"]
    assert unacked_after == 0


# -- session tokens ---------------------------------------------------------


def test_session_token_round_trips(tmp_path):
    token = daemon.create_session(tmp_path)
    assert daemon.verify_session(tmp_path, token) is True


def test_session_token_rejects_wrong_token(tmp_path):
    daemon.create_session(tmp_path)
    assert daemon.verify_session(tmp_path, "wrong-token") is False


def test_session_token_rejects_missing_file(tmp_path):
    assert daemon.verify_session(tmp_path, "anything") is False


def test_session_token_expires(tmp_path):
    now = datetime.now(timezone.utc)
    token = daemon.create_session(tmp_path, ttl_minutes=5, now=now)
    still_valid = daemon.verify_session(tmp_path, token, now=now + timedelta(minutes=4))
    expired = daemon.verify_session(tmp_path, token, now=now + timedelta(minutes=6))
    assert still_valid is True
    assert expired is False


def test_rebuild_matches_live_after_reconcile(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=3,
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    daemon.reconcile_leases(conn)
    assert rebuild.diff_state(conn) == {}


def test_new_session_invalidates_old_token(tmp_path):
    old = daemon.create_session(tmp_path)
    daemon.create_session(tmp_path)
    assert daemon.verify_session(tmp_path, old) is False
