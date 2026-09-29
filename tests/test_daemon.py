"""P2 acceptance #2: expired leases revert to ready (+attempts) on
reconcile-on-start. Also covers queue draining and the in-memory
session token (`SessionManager`, v4 section 8a control 5/6 -- see
tests/test_daemon_security.py for the live-daemon HTTP-layer coverage
of the daemon security controls themselves)."""

import threading
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


def test_reconcile_reverts_expired_lease_to_ready_with_lease_expiries_incremented(conn, project):
    """v4 section 3/5 (changed from v3): a daemon-reclaimed expired lease
    increments `lease_expiries`, not `attempts` -- "v3 conflated them, so
    a daemon restart could burn a node's retries." `attempts` (the
    *agent*-failure counter `core.nodes.fail` owns) must be untouched by
    a lease reclaim."""
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
    assert row["attempts"] == 0
    assert row["lease_expiries"] == 1
    # The consequence of the clock (a lease got reclaimed) is replayable
    # even though the clock itself (lease_until) is not -- v4 section 3.
    events = [dict(e) for e in conn.execute("SELECT * FROM events ORDER BY id")]
    assert any(e["type"] == "node.lease_expired" for e in events)


def test_reconcile_never_moves_to_failed_no_matter_how_many_times_it_runs(conn, project):
    """v4 section 3: "lease_expiries counts crash/timeout reclaims and
    never drives failed." Regression test for the v3 bug this session
    fixes: repeatedly reclaiming the same node's expired lease -- even
    past what would have been `max_attempts` if it were counted as
    `attempts` -- must never route it to `failed`. Only `core.nodes.fail`
    (an agent failure) may do that, and it only ever looks at
    `attempts`, never `lease_expiries`."""
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=1,
    )
    for _ in range(5):
        nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
        daemon.reconcile_leases(conn)
        row = nodes.get_node(conn, task["id"])
        assert row["status"] == "ready", "a lease reclaim must never drive failed"
        assert row["attempts"] == 0
    assert nodes.get_node(conn, task["id"])["lease_expiries"] == 5

    # A *real* agent failure, by contrast, still counts against attempts
    # and still drives failed once max_attempts is hit (max_attempts=1
    # here) -- lease_expiries and attempts are independent counters.
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=60)
    nodes.fail(conn, task["id"], owner="agent-1", lesson="genuine agent failure", trigger="t", do_instead="d", scope="s")
    row = nodes.get_node(conn, task["id"])
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["lease_expiries"] == 5


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


def test_reconcile_on_start_reverts_expired_leases(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready",
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    result = daemon.reconcile_on_start(conn)
    assert len(result["reverted_nodes"]) == 1
    result2 = daemon.reconcile_on_start(conn)
    assert result2["reverted_nodes"] == []


def test_reconcile_on_start_preserves_unacked_inbox_events(conn, project):
    """Regression: reconcile used to mark the oldest 100 unacked events
    of *any* type as acked, and the inbox treats "unacked" as "open" --
    so every `serve` restart silently cleared unattributed-commit,
    signal and audit items. A restart must leave them open."""
    from muvue.core import events as events_mod

    event_id = events_mod.record_event(
        conn, project_id=project["id"], node_id=None, actor="hook",
        type_="unattributed_commit", payload={"sha": "abc"},
    )
    daemon.reconcile_on_start(conn)
    assert events_mod.get_event(conn, event_id)["acked_at"] is None


def test_reconcile_on_start_drains_hook_spool(conn, project, tmp_path):
    queue = tmp_path / ".muvue" / "queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text('{"event": "session-start"}\n')
    result = daemon.reconcile_on_start(conn, repo_root=tmp_path)
    assert result["drained_spool"] == 1
    assert not queue.exists() and not (tmp_path / ".muvue" / "queue.draining").exists()


# -- session tokens (v4 section 8a controls 5/6: in-memory only) -----------


def test_session_token_round_trips():
    session = daemon.SessionManager()
    assert session.verify_and_touch(session.token) is True


def test_session_token_rejects_wrong_token():
    daemon.SessionManager()
    session = daemon.SessionManager()
    assert session.verify_and_touch("wrong-token") is False


def test_session_token_rejects_none():
    session = daemon.SessionManager()
    assert session.verify_and_touch(None) is False


def test_session_token_is_256_bits_of_entropy():
    """`secrets.token_urlsafe(32)` -- 32 random bytes, base64url-encoded
    (v4 section 8a control 5: "mints a random 256-bit token")."""
    session = daemon.SessionManager()
    assert len(session.token) >= 40  # base64url(32 bytes) is 43 chars


def test_session_idle_timeout_expires_after_8h_of_no_activity():
    """Control 6: "idle sessions expire after 8h" -- idle since the
    *last request*, not since issuance: a token used just under the
    idle window keeps working; one left untouched past it stops."""
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    still_valid = session.verify_and_touch(session.token, now=now + timedelta(hours=7, minutes=59))
    assert still_valid is True
    # `verify_and_touch` above already reset the idle clock to
    # now+7:59; a further 8h+ of *no* activity from that point expires it.
    expired = session.verify_and_touch(
        session.token, now=now + timedelta(hours=7, minutes=59) + timedelta(hours=8, minutes=1)
    )
    assert expired is False


def test_session_idle_timeout_resets_on_each_successful_use():
    """A session touched every few hours never idles out, because the
    clock resets on each successful verify -- this is an *idle* timeout,
    not an absolute one (docs/decisions.md documents this choice)."""
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    for hours in range(1, 20):
        ok = session.verify_and_touch(session.token, now=now + timedelta(hours=hours))
        assert ok is True, f"expected still-valid at +{hours}h (touched every 1h)"


def test_new_session_manager_invalidates_old_token():
    """Control 6: "token rotates on `serve` restart" -- a fresh
    `SessionManager` (what a restarted `serve` constructs) has no
    knowledge of a previous instance's token at all."""
    old = daemon.SessionManager()
    new = daemon.SessionManager()
    assert new.verify_and_touch(old.token) is False


def test_rebuild_matches_live_after_reconcile(conn, project):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="ready", max_attempts=3,
    )
    nodes.start(conn, task["id"], owner="agent-1", lease_minutes=-1)
    daemon.reconcile_leases(conn)
    assert rebuild.diff_state(conn) == {}


# -- session auth states (sign-in plan, Task 1) --------------------------------


def test_check_distinguishes_missing_invalid_expired_and_ok():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    assert session.check(None, now=now) == "missing"
    assert session.check("", now=now) == "missing"
    assert session.check("not-the-token", now=now) == "invalid"
    assert session.check(session.token, now=now + timedelta(hours=1)) == "ok"
    assert session.check(session.token, now=now + timedelta(hours=1, minutes=1) + timedelta(hours=8)) == "expired"


def test_check_rejects_non_ascii_token_instead_of_raising():
    session = daemon.SessionManager()
    assert session.check("tok\u201cen") == "invalid"
    assert session.check("\U0001F600") == "invalid"


def test_expired_check_does_not_reset_the_idle_clock():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    later = now + timedelta(hours=9)
    assert session.check(session.token, now=later) == "expired"
    assert session.check(session.token, now=later + timedelta(minutes=1)) == "expired"


def test_consume_nonce_reports_ok_invalid_and_expired():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    nonce = session.mint_nonce()
    assert session.consume_nonce(nonce, now=now) == "ok"
    assert session.consume_nonce(nonce, now=now) == "invalid"  # single use
    assert session.consume_nonce("never-minted", now=now) == "invalid"
    assert session.consume_nonce(None, now=now) == "invalid"
    stale = session.mint_nonce()
    assert session.consume_nonce(stale, now=now + timedelta(hours=9)) == "expired"
    assert session.consume_nonce(stale, now=now + timedelta(hours=9)) == "invalid"  # spent either way


def test_relink_keeps_a_live_token_and_mints_a_working_nonce():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    token = session.token
    nonce, renewed = session.relink(now=now + timedelta(hours=2))
    assert renewed is False
    assert session.token == token
    assert session.consume_nonce(nonce, now=now + timedelta(hours=2)) == "ok"


def test_relink_after_expiry_renews_with_a_new_token_and_kills_the_old_one():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    old_token = session.token
    old_nonce = session.mint_nonce()
    later = now + timedelta(hours=9)
    nonce, renewed = session.relink(now=later)
    assert renewed is True
    assert session.token != old_token
    assert session.check(old_token, now=later) == "invalid"
    assert session.consume_nonce(old_nonce, now=later) == "invalid"
    assert session.consume_nonce(nonce, now=later) == "ok"
    assert session.check(session.token, now=later) == "ok"


def test_check_without_touch_authenticates_but_leaves_the_idle_clock():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = daemon.SessionManager(now=start)
    later = start + timedelta(hours=1)
    assert session.check(session.token, now=later, touch=False) == "ok"
    assert session.last_activity == start
    assert session.check(session.token, now=later) == "ok"
    assert session.last_activity == later


def test_check_without_touch_still_reports_expired():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = daemon.SessionManager(now=start)
    assert session.check(session.token, now=start + timedelta(hours=9), touch=False) == "expired"


def test_consume_nonce_spends_and_checks_in_one_lock_hold():
    class CountingLock:
        def __init__(self):
            self.acquired = 0
            self._lock = threading.Lock()

        def __enter__(self):
            self.acquired += 1
            return self._lock.__enter__()

        def __exit__(self, *exc):
            return self._lock.__exit__(*exc)

    session = daemon.SessionManager()
    nonce = session.mint_nonce()
    session._lock = CountingLock()
    assert session.consume_nonce(nonce) == "ok"
    assert session._lock.acquired == 1
