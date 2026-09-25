"""v4 section 1.2 / P0 acceptance: "N concurrent writers through `core`
with `BEGIN IMMEDIATE`, asserting zero `SQLITE_BUSY` escapes and no lost
updates."

Real threads, real `sqlite3.Connection`s (one per thread -- a
`sqlite3.Connection` is not thread-safe to share), against the same
on-disk WAL database file `core.db.connect` opens (`busy_timeout=5000`
already asserted below). This is deliberately not mocked: the whole
point of `BEGIN IMMEDIATE` is a real SQLite locking behavior under real
contention.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import projects, spend


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "muvue.db"
    conn = core_db.init_db(path)
    conn.close()
    return path


def test_connect_still_sets_wal_and_busy_timeout(db_path):
    """`core.db.connect`'s pragmas are exactly what makes `busy_timeout`
    able to rescue a `BEGIN IMMEDIATE` writer waiting on another writer's
    already-held write lock (as opposed to a *deferred* transaction's
    read-to-write upgrade, which busy_timeout does not rescue -- see
    `test_deferred_read_then_write_upgrade_raises_busy_immediately`
    below). Guards against a future edit accidentally dropping either
    pragma."""
    conn = core_db.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_write_txn_concurrent_writers_no_lost_updates_no_busy_escapes(db_path):
    """N threads, each with its own connection, all incrementing the
    *same* `agent_spend` row (`core.spend.record_spend`, which opens its
    write inside `core.db.write_txn` -- `BEGIN IMMEDIATE`) concurrently.

    Before the `write_txn`/`BEGIN IMMEDIATE` fix, `core.spend.
    record_spend`'s read-then-upsert would have opened a *deferred*
    transaction: two threads racing to upgrade from a shared read lock to
    a write lock on the same row could raise `SQLITE_BUSY` immediately on
    that upgrade (not retried by `busy_timeout`, which only governs
    waiting for an *initial* lock -- see
    `test_deferred_read_then_write_upgrade_raises_busy_immediately`), or
    -- under SQLite's non-2PL deferred-transaction semantics -- silently
    lose one thread's increment if the two transactions' read snapshots
    both predate each other's write. `BEGIN IMMEDIATE` acquires the write
    lock up front, so contention is resolved by `busy_timeout`'s retry
    loop instead: every writer eventually gets in, one at a time, and
    none of their increments are lost.
    """
    project_conn = core_db.connect(db_path)
    try:
        project = projects.create_project(project_conn, goal="concurrency test")
        project_id = project["id"]
    finally:
        project_conn.close()

    n_threads = 20
    increments_per_thread = 10
    amount = 1.0

    errors: list[BaseException] = []
    busy_errors: list[sqlite3.OperationalError] = []
    lock = threading.Lock()

    def worker():
        conn = core_db.connect(db_path)
        try:
            for _ in range(increments_per_thread):
                try:
                    spend.record_spend(conn, project_id, "claude", "usd", amount)
                except sqlite3.OperationalError as e:
                    with lock:
                        if "database is locked" in str(e) or "SQLITE_BUSY" in str(e):
                            busy_errors.append(e)
                        else:
                            errors.append(e)
                except BaseException as e:  # pragma: no cover - failure path
                    with lock:
                        errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a writer thread hung -- deadlock under write_txn"

    assert errors == [], f"unexpected errors from concurrent writers: {errors}"
    assert busy_errors == [], (
        f"{len(busy_errors)} SQLITE_BUSY escaped busy_timeout's retry loop -- "
        "write_txn's BEGIN IMMEDIATE should prevent this entirely"
    )

    check_conn = core_db.connect(db_path)
    try:
        final = spend.get_spend(check_conn, project_id, "claude", "usd")
        expected = n_threads * increments_per_thread * amount
        assert final == expected, (
            f"lost updates: expected {expected} (={n_threads}x{increments_per_thread}), "
            f"got {final}"
        )
        # Every increment recorded its own spend.recorded event too --
        # confirms no writer's transaction was silently dropped/rolled
        # back partway (the row total alone could theoretically hide a
        # write that updated the row but lost its event, or vice versa,
        # if the two weren't atomic together).
        n_events = check_conn.execute(
            "SELECT COUNT(*) c FROM events WHERE type = 'spend.recorded'"
        ).fetchone()["c"]
        assert n_events == n_threads * increments_per_thread
    finally:
        check_conn.close()


def test_deferred_read_then_write_upgrade_raises_busy_immediately(db_path, monkeypatch):
    """Demonstrates the mechanism `write_txn`'s `BEGIN IMMEDIATE` exists
    to avoid (v4 section 1.2's own justification): two *deferred*
    transactions that both read first and then try to upgrade to a write
    can raise `SQLITE_BUSY` on the upgrade, which `busy_timeout` does not
    retry (it only waits for an *initial* lock acquisition). This test
    talks to raw `sqlite3` directly -- not through `core.db.write_txn` --
    specifically to reproduce the *old*, pre-fix pattern this session
    replaced (every mutating function used to do exactly this: a plain
    `conn.execute(...)` with the sqlite3 default deferred `isolation_level`)
    and prove it really could fail this way. It is not a test of current
    `muvue.core` code -- see the previous test for that.
    """
    conn_a = sqlite3.connect(str(db_path), isolation_level="")  # sqlite3 default: deferred BEGIN
    conn_b = sqlite3.connect(str(db_path), isolation_level="")
    conn_a.execute("PRAGMA busy_timeout=5000")
    conn_b.execute("PRAGMA busy_timeout=5000")
    try:
        # Both start a deferred transaction with a read (acquires only a
        # SHARED lock -- no conflict yet).
        conn_a.execute("SELECT COUNT(*) FROM projects")
        conn_b.execute("SELECT COUNT(*) FROM projects")

        # conn_a upgrades to a write lock first -- succeeds (no other
        # writer holds a RESERVED/EXCLUSIVE lock yet).
        conn_a.execute("INSERT INTO projects (goal, phase) VALUES ('a', 'planning')")

        # conn_b now tries to upgrade its own already-open deferred
        # transaction from SHARED to a write lock, while conn_a still
        # holds its write lock (uncommitted). This is the read-to-write
        # upgrade race v4 section 1.2 describes: it raises SQLITE_BUSY
        # immediately, not after busy_timeout's retry window, because
        # SQLite cannot silently promote a SHARED lock while another
        # connection already holds a conflicting RESERVED lock without
        # this immediate failure -- busy_timeout's retry loop applies to
        # *acquiring the initial lock*, not to this upgrade path.
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            conn_b.execute("INSERT INTO projects (goal, phase) VALUES ('b', 'planning')")
    finally:
        conn_a.rollback()
        conn_b.rollback()
        conn_a.close()
        conn_b.close()


def test_read_txn_refuses_a_write_made_inside_it(tmp_path):
    """`read_txn` rolls back when it ends, so a write inside it would be
    silently discarded -- it raises instead."""
    from muvue.core import db as core_db
    from muvue.core import projects

    conn = core_db.init_db(tmp_path / "muvue.db")
    try:
        with pytest.raises(RuntimeError, match="write inside read_txn"):
            with core_db.read_txn(conn):
                projects.create_project(conn, goal="nope")
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    finally:
        conn.close()
