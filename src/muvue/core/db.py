"""Single write path: sqlite connection helpers (WAL, busy_timeout, FTS5)
plus the two transaction context managers plan section 1 principle 2
mandates: `read_txn`/`write_txn`.

v4 section 1.2: "Every write transaction is `BEGIN IMMEDIATE`. A deferred
transaction that reads first and writes later raises `SQLITE_BUSY`
immediately on upgrade and is *not* retried by `busy_timeout`." A
default (deferred) `sqlite3` transaction only acquires a write lock at
the first actual write statement -- if two connections both start a
deferred read and then both try to upgrade to a write, one of them hits
`SQLITE_BUSY` on that upgrade, and `busy_timeout` (which only governs
waiting for an *initial* lock, not a deferred-to-exclusive upgrade under
contention from a peer transaction) does not retry it. `BEGIN IMMEDIATE`
sidesteps this entirely by acquiring the write lock (a
`RESERVED` lock) up front, so contention is resolved by `busy_timeout`'s
retry loop instead of an immediate exception -- see docs/decisions.md.

To control exactly when a transaction begins/ends, `connect()` opens
with `isolation_level=None` (sqlite3's "autocommit" mode): the driver
never issues an implicit `BEGIN` before a DML statement, so
`write_txn`/`read_txn` are the only things that ever open one.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schema import SCHEMA_SQL, SCHEMA_VERSION


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas required by plan section 1/2."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create (or open) the muvue database and ensure schema is present."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """The single write path's transaction boundary (plan section 1
    principle 2, working rule 3: "Every mutating operation goes through
    `muvue.core` inside a `write_txn`"). Issues `BEGIN IMMEDIATE` so the
    write lock is acquired up front rather than on a later read-to-write
    upgrade (see module docstring).

    Re-entrant: `muvue.core` functions routinely call other `muvue.core`
    functions that are themselves wrapped in `write_txn` (e.g.
    `core.nodes.fail` calls `core.nodes.add_note`, `core.gates.
    approve_gate2` calls `core.gates.approve_node` in a loop). Nesting a
    second `BEGIN IMMEDIATE` inside an already-open transaction is both
    unnecessary and (for a plain `BEGIN`) an sqlite3 error, so a nested
    call detects `conn.in_transaction` and just yields -- only the
    outermost call actually opens/commits/rolls back the transaction.
    This keeps each public verb atomic as a whole (a failure partway
    through, e.g. `fail`'s attempts-bump *and* its lesson note, rolls
    back together) instead of each inner helper committing its own
    slice early.
    """
    if conn.in_transaction:
        # Nested call: the outermost write_txn owns commit/rollback.
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def read_txn(conn: sqlite3.Connection):
    """Symmetry/clarity counterpart to `write_txn` for a multi-statement
    read that wants a single consistent snapshot (plan section 1
    principle 2: "`core` exposes exactly two context managers"). Not
    load-bearing the way `write_txn` is -- SQLite's own MVCC-ish
    behaviour under WAL already gives a single autocommit statement a
    consistent read, and `connect()`'s `isolation_level=None` means a
    read issued with no transaction open reads the latest committed
    state per-statement. `read_txn` opens `BEGIN DEFERRED` (a real,
    read-only-by-construction transaction: it never upgrades to a write
    lock because nothing inside it writes) so a caller that runs several
    `SELECT`s wants them all against the same snapshot instead of each
    one picking up whatever the last writer just committed mid-read.
    Decision recorded in docs/decisions.md: `read_txn` is a real
    (deferred) transaction, not a no-op, but it is never the thing that
    makes a P0 acceptance criterion pass or fail -- `write_txn` is."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN DEFERRED")
    try:
        yield conn
    finally:
        conn.rollback()
