# Project canvas backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend half of the project canvas: `deps.carries` (what an arrow carries), the endpoints the canvas needs to create projects/specs/tasks/subtasks, remove/edit them, launch an agent breakdown, launch a full run, and read agent status and per-node run history — all with no frontend changes.

**Architecture:** Extend the existing schema/migration/event/CLI patterns rather than add new ones: `carries` rides along `depends_on` exactly where dep rows are already written; new endpoints are bare-`Body(...)` FastAPI routes inside `create_app`, matching every existing endpoint; breakdown is a new internal CLI verb (`muvue _breakdown`) spawned as a detached, runner-registered subprocess — the same shape `start?agent` already uses for `muvue run`, not a change to `core/runner.py`'s ready-node loop (which deliberately never touches spec nodes).

**Tech Stack:** Python 3.12, FastAPI, sqlite3, Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-27-project-canvas-design.md` (API changes and Agent breakdown sections; this plan implements those two sections only — the frontend canvas is a separate plan, written after this one ships).

## Global Constraints

- Every new mutating endpoint: session-authenticated (`_require_session`), JSON-only (enforced by existing `SecurityMiddleware`, nothing to add), dedupes on `X-Request-Id` via `core.idempotency.once(..., atomic=True)`, and its write happens inside `write_txn` (either directly or via `idempotency.once`'s own wrapping).
- No Pydantic models. Every POST body field is a bare `Body(...)` parameter, matching all ~30 existing endpoints in `src/muvue/api/app.py`.
- New route functions are defined inside `create_app(...)` in `src/muvue/api/app.py`, in the same closure scope as the existing routes, so they can call `_require_session`, `_conn`, `_handle_core_error`, `_request_id`, and reach `config`/`repo_root` directly.
- Any new core exception class must be added to `_handle_core_error`'s tuple in `app.py`, or it will surface as a 500 instead of a 409.
- `SCHEMA_VERSION` bumps from 7 to 8. Both `src/muvue/core/schema.py` (the `CREATE TABLE` for new DBs) and `src/muvue/core/migrate.py` (the `_add_column_if_missing` step for existing DBs) must change together.
- `depends_on` becomes a list of `{"id": int, "carries": str | None}` dicts everywhere it is accepted (CLI, API, `create_node`, `replan_add_subtask`) — not two parallel lists — so every caller passes carries the same way.
- Breakdown never runs inside `core/runner.py`'s `_ready_nodes` loop (it explicitly filters `kind IN ('task', 'subtask')` and excludes `spec`, by design — see `runner.py:207-213`). It gets its own subprocess path.
- `protocol_version` (wherever it's currently defined — Task 8 confirms the exact constant) is bumped once, in the docs task, after all endpoints exist.

---

### Task 1: `deps.carries` schema and migration

**Files:**
- Modify: `src/muvue/core/schema.py` (SCHEMA_VERSION, `CREATE TABLE deps`)
- Modify: `src/muvue/core/migrate.py` (migration step)
- Test: `tests/test_migrate.py` (create if it doesn't exist; otherwise add to the existing migration test file — check first with `ls tests/test_migrate.py`)

**Interfaces:**
- Produces: `deps.carries TEXT` column, nullable, on both a freshly created DB and a migrated one. `SCHEMA_VERSION = 8`.

- [ ] **Step 1: Write the failing test**

First check whether `tests/test_migrate.py` exists:

```bash
ls tests/test_migrate.py
```

If it exists, read it to match its exact fixture style before adding the test below. If it doesn't exist, create it with this content (adjust imports to match `tests/test_api.py`'s style if `core_db`/`core.migrate` are imported differently there — check `tests/test_api.py`'s import block first):

```python
import sqlite3
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import migrate as migrate_mod
from muvue.core.schema import SCHEMA_VERSION


def _init_repo(tmp_path: Path) -> Path:
    (tmp_path / ".muvue").mkdir()
    return tmp_path


def test_deps_gains_carries_column_on_migrate(tmp_path):
    repo_root = _init_repo(tmp_path)
    db_path = repo_root / ".muvue" / "muvue.db"
    # Simulate a pre-existing DB at schema version 7: init at the
    # current schema, then drop the column migrate.py is about to add,
    # so migrate has real work to do.
    conn = core_db.init_db(db_path)
    conn.execute("ALTER TABLE deps RENAME TO deps_old")
    conn.execute(
        "CREATE TABLE deps (node_id INTEGER NOT NULL, depends_on INTEGER NOT NULL, "
        "PRIMARY KEY (node_id, depends_on))"
    )
    conn.execute("INSERT INTO deps SELECT node_id, depends_on FROM deps_old")
    conn.execute("DROP TABLE deps_old")
    conn.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    result_version = migrate_mod.run_migrate(repo_root)

    assert result_version == SCHEMA_VERSION
    conn = core_db.connect(db_path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(deps)")}
    assert "carries" in cols
    conn.close()


def test_new_db_has_carries_column(tmp_path):
    repo_root = _init_repo(tmp_path)
    conn = core_db.init_db(repo_root / ".muvue" / "muvue.db")
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(deps)")}
    assert "carries" in cols
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migrate.py -v`
Expected: FAIL — `assert "carries" in cols` fails (column doesn't exist yet).

- [ ] **Step 3: Bump SCHEMA_VERSION and the table definition**

In `src/muvue/core/schema.py`, change:

```python
SCHEMA_VERSION = 7
```

to:

```python
SCHEMA_VERSION = 8
```

Find the version-history comment block above `SCHEMA_VERSION` (read the file to see its exact existing style — it documents each version bump) and add one line in that same style:

```
# 7 -> 8 (project canvas plan): `deps.carries` records what a
# dependency edge carries (e.g. "audio frames"), shown as an arrow
# label on the dashboard's flow diagram. Nullable: existing deps rows
# and CLI/API calls that don't pass it keep working unlabeled.
```

Then change:

```sql
CREATE TABLE IF NOT EXISTS deps (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    depends_on INTEGER NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (node_id, depends_on)
);
```

to:

```sql
CREATE TABLE IF NOT EXISTS deps (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    depends_on INTEGER NOT NULL REFERENCES nodes(id),
    carries TEXT,
    PRIMARY KEY (node_id, depends_on)
);
```

- [ ] **Step 4: Add the migration step**

In `src/muvue/core/migrate.py`, inside `run_migrate`'s `if current < SCHEMA_VERSION:` block, add one line alongside the existing `_add_column_if_missing` calls:

```python
_add_column_if_missing(conn, "deps", "carries", "TEXT")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_migrate.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Run the full test suite to check nothing else assumed `SCHEMA_VERSION == 7`**

Run: `uv run pytest -q`
Expected: PASS. If anything asserts the literal number 7 (grep first: `grep -rn "SCHEMA_VERSION\|== 7" tests/`), update it to 8 — that assertion exists to catch exactly this kind of drift, not to block it.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/schema.py src/muvue/core/migrate.py tests/test_migrate.py
git commit -m "Add deps.carries column (schema 7 -> 8)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 2: `dep.added` event, `carries` threaded through `create_node`/`replan_add_subtask`, and CLI `--carries`

**Files:**
- Modify: `src/muvue/core/nodes.py` (`create_node`'s `depends_on` handling)
- Modify: `src/muvue/core/revisions.py` (`replan_add_subtask`)
- Modify: `src/muvue/cli/main.py` (`decompose`, `replan` commands)
- Test: `tests/test_nodes.py` (add if the file exists — check with `ls tests/test_nodes.py`; otherwise add to `tests/test_api.py` near other `create_node` tests — grep `def test.*create_node` first to find the right file)

**Interfaces:**
- Consumes: `deps.carries` column from Task 1.
- Produces: `create_node(..., depends_on: list[dict] | None = None, ...)` where each dict is `{"id": int, "carries": str | None}`. `replan_add_subtask(..., depends_on: list[dict] | None = None, ...)` same shape. A `dep.added` event with payload `{"node_id": int, "depends_on": int, "carries": str | None}`. This is the exact shape Task 3 (rebuild replay), Task 6 (the `/children` endpoint) and the frontend plan's "receives from" form will all pass and expect.

- [ ] **Step 1: Write the failing test**

First find where `create_node`'s `depends_on` behavior is already tested:

```bash
grep -rn "depends_on" tests/*.py
```

Add a new test alongside whatever you find (match that file's existing fixture imports/style):

```python
def test_create_node_with_carries_writes_dep_added_event(conn):
    from muvue.core import events as events_mod
    from muvue.core import nodes as nodes_mod
    from muvue.core import projects as projects_mod

    project = projects_mod.create_project(conn, goal="flow test")
    upstream = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="Record voice",
    )
    downstream = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="Detect pitch",
        depends_on=[{"id": upstream["id"], "carries": "audio frames"}],
    )

    dep_row = conn.execute(
        "SELECT * FROM deps WHERE node_id = ? AND depends_on = ?",
        (downstream["id"], upstream["id"]),
    ).fetchone()
    assert dep_row["carries"] == "audio frames"

    events = events_mod.find_recent_by_request_id  # confirm import path exists; see note below
    dep_events = conn.execute(
        "SELECT * FROM events WHERE type = 'dep.added' AND node_id = ?",
        (downstream["id"],),
    ).fetchall()
    assert len(dep_events) == 1
    payload = json.loads(dep_events[0]["payload_json"])
    assert payload == {"node_id": downstream["id"], "depends_on": upstream["id"], "carries": "audio frames"}
```

Add `import json` at the top of the test file if it isn't already imported. Delete the unused `events = events_mod.find_recent_by_request_id` line — it was only there to sanity-check the import path while drafting; the test doesn't need it. Also add a `conn` fixture at the top of the file if one doesn't already exist there (copy the exact fixture from `tests/test_api.py`, shown in this plan's Task 6).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_nodes.py -k carries -v` (adjust the file name to whichever file you added the test to)
Expected: FAIL — either a `TypeError` (depends_on isn't dict-shaped yet) or `carries` column assertion / missing `dep.added` event.

- [ ] **Step 3: Update `create_node`'s depends_on handling**

In `src/muvue/core/nodes.py`, find:

```python
def create_node(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    kind: str,
    title: str,
    parent_id: int | None = None,
    body_md: str = "",
    criteria: list[str] | None = None,
    criteria_mode: str = "manual",
    risk_tier: str = "low",
    max_attempts: int = 3,
    status: str = "pending",
    actor: str = "human",
    actor_evidence: str = "tty",
    predicted_touches: list[str] | None = None,
    depends_on: list[int] | None = None,
    owner: str | None = None,
) -> sqlite3.Row:
```

Change the `depends_on` parameter's type annotation:

```python
    depends_on: list[dict] | None = None,
```

Then find the loop that writes `deps` rows (right after the `node.created` event):

```python
for dep_id in depends_on or []:
    dep = get_node(conn, dep_id)
    if dep["project_id"] != project_id or dep["deleted_at"] is not None:
        raise NodeError(
            f"node {node_id} cannot depend on node {dep_id}: dependencies must be "
            "live nodes in the same project"
        )
    conn.execute(
        "INSERT OR IGNORE INTO deps (node_id, depends_on) VALUES (?, ?)", (node_id, dep_id)
    )
```

Replace it with:

```python
for dep in depends_on or []:
    dep_id = dep["id"]
    carries = dep.get("carries")
    dep_node = get_node(conn, dep_id)
    if dep_node["project_id"] != project_id or dep_node["deleted_at"] is not None:
        raise NodeError(
            f"node {node_id} cannot depend on node {dep_id}: dependencies must be "
            "live nodes in the same project"
        )
    conn.execute(
        "INSERT OR IGNORE INTO deps (node_id, depends_on, carries) VALUES (?, ?, ?)",
        (node_id, dep_id, carries),
    )
    events.record_event(
        conn, project_id=project_id, node_id=node_id, actor=actor,
        actor_evidence=actor_evidence, type_="dep.added",
        payload={"node_id": node_id, "depends_on": dep_id, "carries": carries},
    )
```

Update the function's docstring line that mentions `depends_on` (currently: `` `depends_on` writes `deps` edges (plan section 3) from the new node to live nodes of the same project, each logged as a replayable `dep.added` event.``) — it already says "replayable `dep.added` event", so it just needs to keep matching reality; no further text change needed there since the event now genuinely exists.

- [ ] **Step 4: Update every caller of `create_node` that passes `depends_on`**

```bash
grep -rn "depends_on=" src/muvue/
```

For each call site passing a plain list of ints, wrap each int as `{"id": i, "carries": None}`. Specifically, `src/muvue/cli/main.py`'s `decompose` command currently has:

```python
    depends_on: list[int] = typer.Option([], "--depends-on"),
```

Add a parallel option and combine them before calling `create_node`:

```python
    depends_on: list[int] = typer.Option([], "--depends-on"),
    carries: list[str] = typer.Option([], "--carries", help="what each --depends-on edge carries, same order, or omit to leave unlabeled"),
```

And where it builds the call to `create_node` inside `decompose`'s `_apply()`:

```python
            result = core.nodes.create_node(
                conn, project_id=spec_node["project_id"], kind="task", title=title,
                parent_id=spec_id, body_md=body, criteria=list(criteria),
                criteria_mode=criteria_mode, predicted_touches=list(predicted_touches),
                depends_on=list(depends_on), status="pending", actor="agent",
                actor_evidence=_evidence(),
            )
```

Change `depends_on=list(depends_on)` to build the dict-shaped list, validating the two lists line up:

```python
            if carries and len(carries) != len(depends_on):
                raise typer.BadParameter("--carries must be given once per --depends-on, in the same order (or not at all)")
            dep_list = [
                {"id": dep_id, "carries": carries[i] if i < len(carries) else None}
                for i, dep_id in enumerate(depends_on)
            ]
            result = core.nodes.create_node(
                conn, project_id=spec_node["project_id"], kind="task", title=title,
                parent_id=spec_id, body_md=body, criteria=list(criteria),
                criteria_mode=criteria_mode, predicted_touches=list(predicted_touches),
                depends_on=dep_list, status="pending", actor="agent",
                actor_evidence=_evidence(),
            )
```

Move the `raise typer.BadParameter` check to before the `_apply()` closure is defined (right after the function signature), not inside it, so it fails fast before any DB connection opens — match wherever this function already validates other arguments up front (read the full `decompose` function to place it consistently).

- [ ] **Step 5: Update `replan_add_subtask`**

In `src/muvue/core/revisions.py`, `replan_add_subtask` already forwards `depends_on` straight to `create_node`:

```python
        row = nodes_mod.create_node(
            conn, project_id=parent["project_id"], parent_id=parent_task_id,
            kind="subtask", title=title, body_md=body_md, criteria=criteria,
            depends_on=depends_on, predicted_touches=predicted_touches,
            status="pending" if reasons else "ready",
            actor=actor, actor_evidence=actor_evidence,
        )
```

No change needed here — it's a pure pass-through, and `create_node` now expects the dict shape, so `replan_add_subtask`'s own `depends_on: list[int] | None = None` type hint must change to `depends_on: list[dict] | None = None` to match (its callers must now pass the dict shape too). Update the type hint in its signature. Then find the CLI `replan` command in `src/muvue/cli/main.py` and apply the identical `--carries` option + list-zip pattern from Step 4 to it.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_nodes.py -k carries -v` (or wherever you put the test)
Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS. Fix any other test that calls `create_node(..., depends_on=[<ints>])` or `replan_add_subtask(..., depends_on=[<ints>])` — grep `depends_on=\[` in `tests/` and update each to the dict shape.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/nodes.py src/muvue/core/revisions.py src/muvue/cli/main.py tests/
git commit -m "Add dep.added event and carries labels to dependency edges

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 3: `rebuild` replays `deps`/`carries`

**Files:**
- Modify: `src/muvue/core/rebuild.py`
- Test: `tests/test_rebuild.py` (check it exists first: `ls tests/test_rebuild.py`)

**Interfaces:**
- Consumes: `dep.added` events from Task 2.
- Produces: `rebuild(...)` (read its exact current signature from `src/muvue/core/rebuild.py` before writing this task's code — it is not reproduced here since Task 2's research did not capture it; read the file directly) reconstructs `deps` rows including `carries` from `dep.added` events, matching how it already reconstructs other tables from other event types.

- [ ] **Step 1: Read the current rebuild implementation**

```bash
cat src/muvue/core/rebuild.py
```

Find the loop or dispatch table that handles each event type (e.g. a big `if event["type"] == "node.created": ...` chain, or a `HANDLERS = {"node.created": ..., ...}` dict — read the file to see which). This tells you exactly where to add a `dep.added` handler and what helper (if any) already exists for inserting into a table during replay.

- [ ] **Step 2: Write the failing test**

```bash
ls tests/test_rebuild.py
```

Read its existing test style (fixtures, how it seeds events and calls rebuild, how it asserts). Add a test in that same style asserting: after creating a project, two tasks, and a dependency between them with a `carries` label, then wiping and rebuilding the DB from the event log, the `deps` row (including `carries`) is present in the rebuilt DB. Use `tests/test_nodes.py`'s Task 2 test as the setup template for creating the dependency:

```python
def test_rebuild_reconstructs_deps_with_carries(tmp_path):
    # Adapt this setup to match this file's existing rebuild-test
    # pattern exactly (how it gets a repo_root/db_path, how it invokes
    # rebuild, and what it asserts against afterward) -- read at least
    # one existing test in this file first and mirror its structure.
    ...
    conn.execute("DELETE FROM deps")  # simulate the rows being gone
    conn.commit()
    core.rebuild.rebuild(repo_root)  # exact call signature: see Step 1's read
    conn = core_db.connect(db_path)
    row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?",
        (downstream_id, upstream_id),
    ).fetchone()
    assert row is not None
    assert row["carries"] == "audio frames"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_rebuild.py -k carries -v`
Expected: FAIL — no `deps` row after rebuild (nothing replays `dep.added` yet).

- [ ] **Step 4: Add the `dep.added` replay handler**

Using the dispatch mechanism found in Step 1, add handling for `dep.added` events: for each such event, `INSERT OR IGNORE INTO deps (node_id, depends_on, carries) VALUES (?, ?, ?)` from the event's `payload["node_id"]`, `payload["depends_on"]`, `payload["carries"]`. Match the exact style (a dict entry, an `elif` branch, or a separate function) that the file already uses for a comparably simple one-table-insert event type — do not invent a new dispatch pattern if one already exists.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_rebuild.py -k carries -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/rebuild.py tests/test_rebuild.py
git commit -m "Replay deps/carries during rebuild

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 4: `POST /projects` and `POST /projects/{id}/spec`

**Files:**
- Modify: `src/muvue/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `core.projects.create_project(conn, *, goal, actor, actor_evidence, repo_root, follows, supersedes)`; `core.gates.submit_spec(conn, *, project_id, title, body_md, actor_evidence)` (called by the CLI `spec` command today — same call here).
- Produces: `POST /projects {"goal": str}` → the created project dict, same shape as `GET /projects/{id}`. `POST /projects/{id}/spec {"title": str, "body_md": str}` → the created spec node dict, same shape as `GET /nodes/{id}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`, using the file's existing `client`/`auth_headers`/`conn` fixtures (shown at the top of the file):

```python
def test_create_project_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "voxscore: voice to sheet music"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["goal"] == "voxscore: voice to sheet music"
    assert body["project"]["phase"] == "planning"
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (body["project"]["id"],)).fetchone()
    assert row is not None


def test_create_spec_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(
        f"/projects/{project_id}/spec",
        json={"title": "Voxscore", "body_md": "Record voice.\nDetect pitch."},
        headers=auth_headers,
    )
    assert r.status_code == 200
    node = r.json()["node"] if "node" in r.json() else r.json()
    assert node["kind"] == "spec"
    assert node["title"] == "Voxscore"
    assert node["status"] == "pending"


def test_create_project_requires_auth(client):
    r = client.post("/projects", json={"goal": "g"})
    assert r.status_code == 403


def test_create_project_requires_json(client, auth_headers):
    r = client.post("/projects", data="goal=g", headers={**auth_headers, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code in (400, 415, 422)
```

Before finalizing the assertions on response shape, run `grep -n "def submit_spec" -A25 src/muvue/core/gates.py` and check what dict shape it actually returns (a bare node row, or `{"node": ...}`) — match the assertions to that, don't guess.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "create_project or create_spec" -v`
Expected: FAIL with 404 (routes don't exist yet).

- [ ] **Step 3: Add the endpoints**

In `src/muvue/api/app.py`, find the existing `/projects` GET routes (`app.py:488-501` per the earlier read) and add these two POST routes near them, inside `create_app`:

```python
    @app.post("/projects")
    def create_project(request: Request, goal: str = Body(...)) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "project-create",
                    lambda: core.projects.create_project(
                        conn, goal=goal, actor="human", actor_evidence="dashboard_token",
                        repo_root=repo_root,
                    ),
                )
            except Exception as e:
                _handle_core_error(e)
        return {"project": _row_to_dict(result)}

    @app.post("/projects/{project_id}/spec")
    def create_spec(
        project_id: int, request: Request, title: str = Body(...), body_md: str = Body(...)
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "spec",
                    lambda: core.gates.submit_spec(
                        conn, project_id=project_id, title=title, body_md=body_md,
                        actor_evidence="dashboard_token",
                    ),
                )
            except Exception as e:
                _handle_core_error(e)
        return {"node": _row_to_dict(result) if isinstance(result, sqlite3.Row) else result}
```

Adjust the return statements once Step 1's inspection of `create_project`'s and `submit_spec`'s actual return types is done — `create_project` returns a `sqlite3.Row` per its signature (confirmed), so `_row_to_dict(result)` is correct for it. Check `submit_spec`'s actual return type the same way and simplify the `create_spec` return line to match exactly (no `isinstance` guess-branch in the final code — that line above is a placeholder for you to resolve during this step, and must not survive into the commit).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k "create_project or create_spec" -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/muvue/api/app.py tests/test_api.py
git commit -m "Add POST /projects and POST /projects/{id}/spec

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 5: `POST /nodes/{id}/children` (task under spec, subtask under task, gate-aware)

**Files:**
- Modify: `src/muvue/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `core.nodes.create_node` (Task 2's dict-shaped `depends_on`), `core.revisions.replan_add_subtask` (Task 2), `core.nodes.get_node`.
- Produces: `POST /nodes/{id}/children {"title": str, "body_md": str, "criteria": [str], "depends_on": [{"id": int, "carries": str|None}]}` → the created child node. If `id` is a `spec` node, creates a `task` via `create_node` (mirrors `decompose`). If `id` is a `task` node, creates a `subtask` via `replan_add_subtask` if the task is Gate-2-approved (`criteria_hash is not None`), else via `create_node` directly (mirrors pre-Gate-2 planning, same as adding a task under a spec).

- [ ] **Step 1: Write the failing tests**

```python
def test_add_task_under_pending_spec(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "S", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{spec_id}/children", json={"title": "Record voice", "body_md": "capture mic"}, headers=auth_headers)
    assert r.status_code == 200
    child = r.json()["node"]
    assert child["kind"] == "task"
    assert child["parent_id"] == spec_id


def test_add_task_with_carries_dependency(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "S", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "Record voice"}, headers=auth_headers)
    upstream_id = r.json()["node"]["id"]

    r = client.post(
        f"/nodes/{spec_id}/children",
        json={"title": "Detect pitch", "depends_on": [{"id": upstream_id, "carries": "audio frames"}]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    downstream_id = r.json()["node"]["id"]
    row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?", (downstream_id, upstream_id)
    ).fetchone()
    assert row["carries"] == "audio frames"


def test_add_subtask_under_approved_task_uses_replan(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/children", json={"title": "sub 1", "predicted_touches": ["a.py"]}, headers=auth_headers)
    assert r.status_code == 200
    child = r.json()["node"]
    assert child["kind"] == "subtask"
    assert child["parent_id"] == task_id
```

`ready_task` is the existing fixture from Task 6's research (creates a project + a Gate-2-approved task) — confirm its exact name/shape by reading `tests/test_api.py`'s fixture block; use whatever it's actually called.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k children -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the endpoint**

```python
    @app.post("/nodes/{node_id}/children")
    def add_child_node(
        node_id: int,
        request: Request,
        title: str = Body(...),
        body_md: str = Body(default=""),
        criteria: list[str] = Body(default=[]),
        predicted_touches: list[str] = Body(default=[]),
        depends_on: list[dict] = Body(default=[]),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                def _apply():
                    parent = core.nodes.get_node(conn, node_id)
                    if parent["kind"] == "spec":
                        return core.nodes.create_node(
                            conn, project_id=parent["project_id"], parent_id=node_id,
                            kind="task", title=title, body_md=body_md, criteria=criteria,
                            predicted_touches=predicted_touches, depends_on=depends_on,
                            status="pending", actor="human", actor_evidence="dashboard_token",
                        )
                    if parent["kind"] != "task":
                        raise core.nodes.NodeError(f"node {node_id} is kind={parent['kind']!r}; children can only be added to a spec or a task")
                    if parent["criteria_hash"] is not None:
                        return core.revisions.replan_add_subtask(
                            conn, parent_task_id=node_id, title=title, body_md=body_md,
                            criteria=criteria, depends_on=depends_on,
                            predicted_touches=predicted_touches, config=config,
                            actor="human", actor_evidence="dashboard_token",
                        )
                    return core.nodes.create_node(
                        conn, project_id=parent["project_id"], parent_id=node_id,
                        kind="subtask", title=title, body_md=body_md, criteria=criteria,
                        predicted_touches=predicted_touches, depends_on=depends_on,
                        status="pending", actor="human", actor_evidence="dashboard_token",
                    )

                result = core.idempotency.once(conn, _request_id(request), "children", _apply)
            except Exception as e:
                _handle_core_error(e)
        return {"node": dict(result) if not isinstance(result, dict) else result}
    ```

Run `grep -n "def once" -A15 src/muvue/core/idempotency.py` to confirm `once()` accepts a zero-arg callable that itself does multiple core calls with branching logic (it does — `fn: Callable[[], object]`), so wrapping the whole `_apply` branch inside one `once()` call is correct and matches the existing `comment`/`propose-revision` pattern.

Simplify the final `return` line once you've confirmed both `create_node` (returns `sqlite3.Row`) and `replan_add_subtask` (returns `dict`, per Task 2's read) — normalize both to a plain `dict` without the `isinstance` branch surviving into the commit (same instruction as Task 4 Step 3).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k children -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/muvue/api/app.py tests/test_api.py
git commit -m "Add POST /nodes/{id}/children for tasks and subtasks

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 6: `POST /nodes/{id}/remove` and `POST /nodes/{id}/edit`

**Files:**
- Create: `src/muvue/core/removal.py`
- Modify: `src/muvue/api/app.py`
- Test: `tests/test_api.py`, `tests/test_removal.py`

**Interfaces:**
- Produces: `core.removal.remove_node(conn, node_id, *, actor, actor_evidence) -> dict` — soft-deletes the node and every non-deleted descendant (matching `parent_id` transitively) by setting `deleted_at`, refuses if the node's `criteria_hash is not None` (already Gate-2-approved) with a new `core.removal.RemovalError`, and records a `node.removed` event per node removed. `POST /nodes/{id}/remove` and `POST /nodes/{id}/edit {title?, body_md?, criteria?}` (edits before Gate 2 via the existing criteria-edit path; after Gate 2, returns 409 pointing at `propose-revision` — find the exact existing criteria-edit core function first).

- [ ] **Step 1: Find the existing pre-Gate-2 criteria edit path**

```bash
grep -n "def edit_criteria\|criteria_hash is None" src/muvue/core/gates.py
```

Read the matching function in full — this is what `POST /nodes/{id}/edit` calls for the pre-Gate-2 case.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_removal.py
import pytest

from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod
from muvue.core import removal


def test_remove_node_soft_deletes_node_and_children(conn):
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s")
    task = nodes_mod.create_node(conn, project_id=project["id"], kind="task", title="t", parent_id=spec["id"])
    sub = nodes_mod.create_node(conn, project_id=project["id"], kind="subtask", title="sub", parent_id=task["id"])

    removal.remove_node(conn, task["id"], actor="human", actor_evidence="tty")

    assert nodes_mod.get_node(conn, task["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, sub["id"])["deleted_at"] is not None
    assert nodes_mod.get_node(conn, spec["id"])["deleted_at"] is None


def test_remove_node_refuses_after_gate2(conn, config):
    from muvue.core import gates
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)

    with pytest.raises(removal.RemovalError):
        removal.remove_node(conn, task["id"], actor="human", actor_evidence="tty")
```

Add matching API-level tests to `tests/test_api.py`:

```python
def test_remove_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{task_id}/remove", headers=auth_headers)
    assert r.status_code == 200
    assert conn.execute("SELECT deleted_at FROM nodes WHERE id = ?", (task_id,)).fetchone()["deleted_at"] is not None


def test_remove_endpoint_refuses_after_gate2(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/remove", headers=auth_headers)
    assert r.status_code == 409


def test_edit_endpoint_before_gate2(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "t"}, headers=auth_headers)
    task_id = r.json()["node"]["id"]

    r = client.post(f"/nodes/{task_id}/edit", json={"title": "renamed"}, headers=auth_headers)
    assert r.status_code == 200
    assert conn.execute("SELECT title FROM nodes WHERE id = ?", (task_id,)).fetchone()["title"] == "renamed"


def test_edit_endpoint_refuses_after_gate2(client, ready_task, auth_headers):
    task_id = ready_task["task"]["id"]
    r = client.post(f"/nodes/{task_id}/edit", json={"title": "x"}, headers=auth_headers)
    assert r.status_code == 409
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_removal.py tests/test_api.py -k "remove or edit_endpoint" -v`
Expected: FAIL (module/routes don't exist).

- [ ] **Step 4: Write `src/muvue/core/removal.py`**

```python
"""Soft-delete a node and its descendants before Gate 2 (project canvas
plan: a task or subtask added by mistake can be removed from the
dashboard, but only while the plan is still being shaped -- once Gate 2
freezes criteria, removing a node needs a plan revision instead, same
as any other post-freeze change)."""

from __future__ import annotations

import sqlite3

from . import db as db_mod
from . import events
from . import nodes as nodes_mod


class RemovalError(Exception):
    pass


def _descendants(conn: sqlite3.Connection, node_id: int) -> list[sqlite3.Row]:
    out = []
    frontier = [node_id]
    while frontier:
        rows = db_mod.query_all(
            conn, "SELECT id FROM nodes WHERE parent_id = ? AND deleted_at IS NULL",
            (frontier.pop(),),
        )
        for row in rows:
            out.append(row)
            frontier.append(row["id"])
    return out


def remove_node(
    conn: sqlite3.Connection, node_id: int, *, actor: str = "human", actor_evidence: str = "tty"
) -> dict:
    with db_mod.write_txn(conn):
        node = nodes_mod.get_node(conn, node_id)
        if node["criteria_hash"] is not None:
            raise RemovalError(
                f"node {node_id} is already Gate-2 approved; propose a plan revision instead of removing it"
            )
        to_remove = [node] + [nodes_mod.get_node(conn, d["id"]) for d in _descendants(conn, node_id)]
        for row in to_remove:
            conn.execute(
                "UPDATE nodes SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (row["id"],),
            )
            events.record_event(
                conn, project_id=row["project_id"], node_id=row["id"], actor=actor,
                actor_evidence=actor_evidence, type_="node.removed", payload={"node_id": row["id"]},
            )
        return {"removed": [r["id"] for r in to_remove]}
```

- [ ] **Step 5: Add the endpoints**

```python
    @app.post("/nodes/{node_id}/remove")
    def remove_node(request: Request, node_id: int) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "remove",
                    lambda: core.removal.remove_node(conn, node_id, actor="human", actor_evidence="dashboard_token"),
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/edit")
    def edit_node(
        request: Request, node_id: int, title: str | None = Body(default=None),
        body_md: str | None = Body(default=None), criteria: list[str] | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                node = core.nodes.get_node(conn, node_id)
                if node["criteria_hash"] is not None:
                    raise core.removal.RemovalError(
                        f"node {node_id} is already Gate-2 approved; use propose-revision instead of editing directly"
                    )
                result = core.idempotency.once(
                    conn, _request_id(request), "edit",
                    lambda: core.gates.edit_criteria(  # confirm exact function name/signature from Step 1's read; adjust args to match
                        conn, node_id, title=title, body_md=body_md, criteria=criteria,
                    ),
                )
            except Exception as e:
                _handle_core_error(e)
        return result
```

Adjust `edit_node`'s call to whatever the real function from Step 1 is named and takes — do not leave the placeholder comment `# confirm exact...` in the committed code; resolve it during this step.

Add `core.removal.RemovalError` to `_handle_core_error`'s tuple of exception types that map to 409 (find the tuple shown in this plan's Global Constraints / the existing `_handle_core_error` body, and add `core.removal.RemovalError` to it).

Add `from . import removal` (or the equivalent relative import used by the rest of `core/__init__.py`) so `core.removal` is reachable — check `src/muvue/core/__init__.py`'s existing import list and add `removal` in the same style.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_removal.py tests/test_api.py -k "remove or edit_endpoint" -v`
Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/removal.py src/muvue/core/__init__.py src/muvue/api/app.py tests/test_removal.py tests/test_api.py
git commit -m "Add node remove and pre-Gate-2 edit endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 7: fake agent breakdown behavior + `parse_breakdown`

**Files:**
- Modify: `src/muvue/fake_agent.py`
- Modify: `src/muvue/core/drivers.py`
- Test: `tests/test_drivers.py` (check exists: `ls tests/test_drivers.py`; else find wherever `parse_fake` is tested)

**Interfaces:**
- Produces: `fake_agent.py` responds to `--behavior breakdown` (or `MUVUE_FAKE_BEHAVIOR=breakdown`) by printing one line `{"type": "breakdown", "children": [...]}` with exactly 3 children, at least one carrying a `depends_on`/`carries` pair. `core.drivers.parse_breakdown(stdout: str) -> list[dict]` parses that line into `[{"title": str, "body_md": str, "criteria": [str], "depends_on": [{"id": int, "carries": str|None}], "predicted_touches": [str]}, ...]`. Since a fresh breakdown's children can't reference each other by a real DB id yet (they don't exist until created), `depends_on` inside a breakdown's own output refers to the *index* (0-based) of an earlier sibling in the same `children` list, not a node id — Task 8's consumer resolves those indices to real ids as it creates each child in order.

- [ ] **Step 1: Read the current fake agent and `parse_fake`**

```bash
cat src/muvue/fake_agent.py
grep -n "def parse_fake" -A20 src/muvue/core/drivers.py
```

- [ ] **Step 2: Write the failing tests**

```python
# wherever parse_fake is tested — mirror that file's imports/style
from muvue.core.drivers import parse_breakdown


def test_parse_breakdown_reads_children():
    stdout = (
        '{"type": "breakdown", "children": ['
        '{"title": "Record voice", "body_md": "capture mic", "criteria": [], "depends_on": [], "predicted_touches": []}, '
        '{"title": "Detect pitch", "body_md": "yin tracker", "criteria": [], '
        '"depends_on": [{"id": 0, "carries": "audio frames"}], "predicted_touches": []}, '
        '{"title": "Export", "body_md": "musicxml", "criteria": [], '
        '"depends_on": [{"id": 1, "carries": "notes"}], "predicted_touches": []}'
        ']}\n'
    )
    children = parse_breakdown(stdout)
    assert len(children) == 3
    assert children[0]["title"] == "Record voice"
    assert children[1]["depends_on"] == [{"id": 0, "carries": "audio frames"}]


def test_parse_breakdown_no_output_returns_empty():
    assert parse_breakdown("") == []
```

Run the fake agent's own breakdown behavior as a subprocess test, matching however `fake_agent.py`'s other behaviors are tested (grep `subprocess.run.*fake_agent\|MUVUE_FAKE_BEHAVIOR` in `tests/`):

```python
def test_fake_agent_breakdown_behavior_prints_three_children():
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "muvue.fake_agent"],
        input="decompose this spec\n", capture_output=True, text=True,
        env={"MUVUE_FAKE_BEHAVIOR": "breakdown"},
    )
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    breakdown_lines = [l for l in lines if l.get("type") == "breakdown"]
    assert len(breakdown_lines) == 1
    assert len(breakdown_lines[0]["children"]) == 3
```

Adjust the invocation (module path, stdin format, env-passing) to match exactly how an existing fake-agent test in this codebase invokes it — read one such test first (grep for `fake_agent` in `tests/`) rather than guessing the CLI entry point.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -k "parse_breakdown or fake_agent_breakdown" -v`
Expected: FAIL.

- [ ] **Step 4: Add `parse_breakdown` to `drivers.py`**

Add this function near `parse_fake` in `src/muvue/core/drivers.py`, reusing whatever private JSON-lines helper `parse_fake` already uses (`_json_lines`, per the existing code):

```python
def parse_breakdown(stdout: str) -> list[dict]:
    """A breakdown agent's line protocol: JSON lines, the last
    `{"type": "breakdown", "children": [...]}` line wins. Each child's
    `depends_on` entries refer to the 0-based index of an earlier
    sibling in the same list (children have no node id yet), resolved
    to real ids by whichever core code creates them in order."""
    objs = [o for o in _json_lines(stdout) if o.get("type") == "breakdown"]
    if not objs:
        return []
    return objs[-1].get("children", [])
```

- [ ] **Step 5: Add the breakdown behavior to `fake_agent.py`**

Add `"breakdown"` to the `BEHAVIORS` tuple. In whichever function dispatches on behavior (read the file to find it — likely `_run()`), add a branch that prints the breakdown line instead of a result line:

```python
    if behavior == "breakdown":
        print(json.dumps({
            "type": "breakdown",
            "children": [
                {"title": "Record voice", "body_md": "Capture microphone input.", "criteria": [], "depends_on": [], "predicted_touches": []},
                {"title": "Detect pitch", "body_md": "Turn audio into a note sequence.", "criteria": [], "depends_on": [{"id": 0, "carries": "audio frames"}], "predicted_touches": []},
                {"title": "Export MusicXML", "body_md": "Write the note sequence to a file.", "criteria": [], "depends_on": [{"id": 1, "carries": "notes"}], "predicted_touches": []},
            ],
        }))
        return
```

Place this branch consistently with how the other behaviors (`cooperative`, `lazy`, etc.) are structured in the same function — match indentation/control-flow style, don't restructure the existing dispatch.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -k "parse_breakdown or fake_agent_breakdown" -v`
Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/fake_agent.py src/muvue/core/drivers.py tests/
git commit -m "Add breakdown behavior to the fake agent and parse_breakdown

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 8: `muvue _breakdown` internal CLI verb

**Files:**
- Modify: `src/muvue/cli/main.py`
- Test: `tests/test_cli_breakdown.py` (create)

**Interfaces:**
- Consumes: `core.drivers.invoke_driver(agent_name, agent_cfg, brief_text, cwd, *, timeout, log_path) -> DriverResult` (existing); `core.drivers.parse_breakdown` (Task 7); `core.nodes.create_node` (Task 2); `core.revisions.replan_add_subtask` (Task 2); `core.runners.register`/`unregister` (existing, used exactly as `core.runner.run` uses them).
- Produces: a hidden Typer command `muvue _breakdown --node ID --agent NAME --path REPO` that: registers itself in the runner registry under the node's project id (so `pause` can kill it), builds a decomposition brief from the node, invokes the named agent once via `invoke_driver`, parses the result with a breakdown-aware parser (built in this task, since real agents don't speak the fake agent's `parse_breakdown` line protocol — see Step 4), creates each child in order (resolving sibling-index `depends_on` to real ids as they're created), records `breakdown.started`/`breakdown.finished`/`breakdown.failed` events, and unregisters on exit. This is what Task 9's API endpoint spawns as a subprocess.

- [ ] **Step 1: Read `core.runner.run`'s register/unregister pattern**

```bash
sed -n 740,762p src/muvue/core/runner.py
```

(Already captured in this plan's research: `runners_mod.register(repo_root, project_id)` before, `runners_mod.unregister(repo_root)` in a `finally`.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_cli_breakdown.py
import json
from pathlib import Path

from typer.testing import CliRunner

from muvue.cli.main import app
from muvue.core import db as core_db
from muvue.core import nodes as nodes_mod
from muvue.core import projects as projects_mod


def _init_repo(tmp_path: Path) -> Path:
    # Match whatever this codebase's CLI tests already use to set up a
    # repo (likely calls the `init` command via CliRunner, or a shared
    # test helper) -- grep "CliRunner" in tests/ and mirror one
    # existing CLI test's setup exactly rather than reinventing it.
    ...


def test_breakdown_creates_tasks_from_fake_agent(tmp_path, monkeypatch):
    repo_root = _init_repo(tmp_path)
    monkeypatch.setenv("MUVUE_FAKE_BEHAVIOR", "breakdown")
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    project = projects_mod.create_project(conn, goal="g")
    spec = nodes_mod.create_node(conn, project_id=project["id"], kind="spec", title="s", status="ready")
    conn.commit()
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["_breakdown", "--node", str(spec["id"]), "--agent", "fake", "--path", str(repo_root)])

    assert result.exit_code == 0, result.output
    conn = core_db.connect(repo_root / ".muvue" / "muvue.db")
    children = conn.execute(
        "SELECT * FROM nodes WHERE parent_id = ? ORDER BY id", (spec["id"],)
    ).fetchall()
    assert len(children) == 3
    assert children[0]["title"] == "Record voice"
    dep_row = conn.execute(
        "SELECT carries FROM deps WHERE node_id = ? AND depends_on = ?",
        (children[1]["id"], children[0]["id"]),
    ).fetchone()
    assert dep_row["carries"] == "audio frames"
    events = conn.execute("SELECT type FROM events WHERE node_id = ? ORDER BY id", (spec["id"],)).fetchall()
    types = [e["type"] for e in events]
    assert "breakdown.started" in types
    assert "breakdown.finished" in types
```

Resolve the `_init_repo` helper by reading one existing CLI test file end to end first (e.g. wherever `decompose`/`spec` CLI commands are tested) and copying its exact repo-setup pattern.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_breakdown.py -v`
Expected: FAIL — no such command `_breakdown`.

- [ ] **Step 4: Add the `_breakdown` command**

Add to `src/muvue/cli/main.py`, near `decompose`/`replan`:

```python
@app.command(name="_breakdown", hidden=True, help="Internal: an agent decomposes a spec or task into children.")
def breakdown(
    node_id: int = typer.Option(..., "--node"),
    agent: str = typer.Option(..., "--agent"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        node = core.nodes.get_node(conn, node_id)
        core_runners = core.runners
        core_runners.register(repo_root, node["project_id"])
        try:
            agent_cfg = config.agents[agent]
            criteria_text = "\n".join(f"- {c}" for c in json.loads(node["criteria_json"]))
            brief = (
                f"Break down this {node['kind']} into 2-5 child tasks, each with a "
                f"title, a one-line body, acceptance criteria, and which earlier "
                f"sibling (by 0-based index) it depends on and what that dependency "
                f"carries. Do not write code.\n\n"
                f"Title: {node['title']}\n\n{node['body_md']}\n\n{criteria_text}\n\n"
                'Reply with exactly one line: {"type": "breakdown", "children": '
                '[{"title": ..., "body_md": ..., "criteria": [...], '
                '"depends_on": [{"id": <sibling index>, "carries": "..."}], '
                '"predicted_touches": [...]}]}'
            )
            log_path = repo_root / core.runner.LOGS_RELDIR / f"breakdown-{node_id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            core.events.record_event(
                conn, project_id=node["project_id"], node_id=node_id, actor="agent",
                actor_evidence="tty", type_="breakdown.started", payload={"agent": agent},
            )
            conn.commit()
            result = core.drivers.invoke_driver(agent, agent_cfg, brief, repo_root, log_path=log_path)
            if result.status != "done":
                core.events.record_event(
                    conn, project_id=node["project_id"], node_id=node_id, actor="agent",
                    actor_evidence="tty", type_="breakdown.failed",
                    payload={"reason": result.error or result.status},
                )
                conn.commit()
                raise typer.Exit(code=1)
            children_spec = core.drivers.parse_breakdown(result.raw_stdout)
            created_ids: list[int] = []
            for child in children_spec:
                resolved_deps = [
                    {"id": created_ids[d["id"]], "carries": d.get("carries")}
                    for d in child.get("depends_on", [])
                ]
                if node["kind"] == "spec":
                    row = core.nodes.create_node(
                        conn, project_id=node["project_id"], parent_id=node_id, kind="task",
                        title=child["title"], body_md=child.get("body_md", ""),
                        criteria=child.get("criteria", []), predicted_touches=child.get("predicted_touches", []),
                        depends_on=resolved_deps, status="pending", actor="agent", actor_evidence="tty",
                    )
                else:
                    row = core.revisions.replan_add_subtask(
                        conn, parent_task_id=node_id, title=child["title"],
                        body_md=child.get("body_md", ""), criteria=child.get("criteria", []),
                        depends_on=resolved_deps, predicted_touches=child.get("predicted_touches", []),
                        config=config, actor="agent", actor_evidence="tty",
                    )
                created_ids.append(row["id"] if isinstance(row, dict) else row["id"])
            core.events.record_event(
                conn, project_id=node["project_id"], node_id=node_id, actor="agent",
                actor_evidence="tty", type_="breakdown.finished", payload={"created": created_ids},
            )
            conn.commit()
        finally:
            core_runners.unregister(repo_root)
    finally:
        conn.close()
```

Check `_load_config`'s exact signature (grep `def _load_config` in `main.py`) and adjust the call if it takes different args than `(repo_root)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_breakdown.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/cli/main.py tests/test_cli_breakdown.py
git commit -m "Add internal muvue _breakdown CLI verb

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 9: `POST /nodes/{id}/breakdown` and `POST /projects/{id}/run`

**Files:**
- Modify: `src/muvue/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `muvue _breakdown` (Task 8), `core.runners.live` (existing, to detect an already-running breakdown for the 409 case), `[routing]` config (existing `RoutingConfig`).
- Produces: `POST /nodes/{id}/breakdown {"agent": str|None}` → `{"spawned": {"pid": int, "node_id": int, "agent": str, "log": str}}`, 409 if a breakdown is already running on that node. `POST /projects/{id}/run {"parallel": int|None}` → `{"spawned": {"pid": int, "project_id": int, "log": str}}`, spawning `muvue run --project ID [--parallel N]` exactly as `_spawn_runner` spawns `muvue run --node ID --agent X`.

- [ ] **Step 1: Write the failing tests**

```python
def test_breakdown_endpoint_spawns_process(client, conn, auth_headers, tmp_path):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    conn.execute("UPDATE nodes SET status = 'ready' WHERE id = ?", (spec_id,))
    conn.commit()

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=auth_headers)
    assert r.status_code == 200
    assert "pid" in r.json()["spawned"]


def test_breakdown_endpoint_409_if_already_running(client, conn, auth_headers, monkeypatch):
    import muvue.core.runners as runners_mod
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    monkeypatch.setattr(runners_mod, "live", lambda repo_root: [{"pid": 1, "project_id": project_id, "started_at": "now"}])

    r = client.post(f"/nodes/{spec_id}/breakdown", json={}, headers=auth_headers)
    assert r.status_code == 409


def test_run_endpoint_spawns_process(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/run", json={}, headers=auth_headers)
    assert r.status_code == 200
    assert "pid" in r.json()["spawned"]
```

Note: `test_breakdown_endpoint_409_if_already_running` monkeypatches `runners_mod.live` to simulate a running process without actually spawning one — this checks the 409 branch is reachable without a slow real subprocess.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "breakdown_endpoint or run_endpoint" -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the endpoints**

```python
    @app.post("/nodes/{node_id}/breakdown")
    def start_breakdown(node_id: int, request: Request, agent: str | None = Body(default=None)) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                node = core.nodes.get_node(conn, node_id)
            except Exception as e:
                _handle_core_error(e)
        if node["kind"] not in ("spec", "task"):
            raise HTTPException(status_code=409, detail=f"node {node_id} is kind={node['kind']!r}; breakdown only applies to a spec or a task")
        resolved_agent = agent or getattr(config.routing, "spec" if node["kind"] == "spec" else "task")
        if resolved_agent not in config.agents:
            raise HTTPException(status_code=422, detail=f"unknown agent {resolved_agent!r} (configured: {sorted(config.agents)})")
        already_running = any(r["project_id"] in (None, node["project_id"]) for r in core.runners.live(repo_root))
        if already_running:
            raise HTTPException(status_code=409, detail=f"a breakdown or run is already active on project {node['project_id']}")
        log_path = repo_root / core.runner.LOGS_RELDIR / f"breakdown-{node_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "muvue", "_breakdown", "--node", str(node_id), "--agent", resolved_agent,
                 "--path", str(repo_root)],
                cwd=repo_root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return {"spawned": {"pid": proc.pid, "node_id": node_id, "agent": resolved_agent,
                            "log": str(log_path.relative_to(repo_root))}}

    @app.post("/projects/{project_id}/run")
    def start_run(project_id: int, request: Request, parallel: int | None = Body(default=None)) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                core.projects.get_project(conn, project_id)
            except Exception as e:
                _handle_core_error(e)
        log_path = repo_root / core.runner.LOGS_RELDIR / f"run-project-{project_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-m", "muvue", "run", "--project", str(project_id), "--path", str(repo_root)]
        if parallel:
            args += ["--parallel", str(parallel)]
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                args, cwd=repo_root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return {"spawned": {"pid": proc.pid, "project_id": project_id,
                            "log": str(log_path.relative_to(repo_root))}}
```

Confirm `muvue run`'s actual `--project` flag name (grep `typer.Option` in the `run` CLI command in `main.py` — it may be `--project-id` or take the project id positionally) and adjust the `args` list to match exactly; do not guess the flag spelling.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k "breakdown_endpoint or run_endpoint" -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/muvue/api/app.py tests/test_api.py
git commit -m "Add POST /nodes/{id}/breakdown and POST /projects/{id}/run

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 10: `GET /agents/status` and `GET /nodes/{id}/runs`

**Files:**
- Create: `src/muvue/core/agent_status.py`
- Modify: `src/muvue/api/app.py`
- Test: `tests/test_agent_status.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `core.agent_status.agent_status(conn, project_id, config) -> list[dict]`, one dict per configured agent: `{"agent": str, "roles": [str], "current": {"node_id": int, "title": str, "lease_until": str} | None, "spend": [{"unit": str, "spent": float}]}`. `GET /agents/status?project_id=N` returns `{"agents": [...]}`. `GET /nodes/{id}/runs` returns `{"runs": [...]}` from existing lease/attempt/event data for that node (attempts recorded via existing events — read `core.events`/`nodes.attempts` to find what's already there before adding new storage).

- [ ] **Step 1: Write the failing test for `agent_status`**

```python
# tests/test_agent_status.py
from muvue.config import MuvueConfig  # confirm exact import path — may be muvue.core.config
from muvue.core import agent_status, nodes as nodes_mod, projects as projects_mod, spend as spend_mod


def test_agent_status_lists_configured_agents_with_roles_and_spend(conn):
    config = MuvueConfig()  # default routes everything to "claude" per RoutingConfig's defaults
    project = projects_mod.create_project(conn, goal="g")
    spend_mod.record_spend(conn, project_id=project["id"], agent="claude", unit="tokens", amount=1.5)  # confirm exact record-spend function name/signature first

    rows = agent_status.agent_status(conn, project["id"], config)

    claude_row = next(r for r in rows if r["agent"] == "claude")
    assert set(claude_row["roles"]) == {"spec", "task", "subtask"}
    assert claude_row["current"] is None
    assert any(s["unit"] == "tokens" and s["spent"] == 1.5 for s in claude_row["spend"])


def test_agent_status_shows_current_work(conn):
    config = MuvueConfig()
    project = projects_mod.create_project(conn, goal="g")
    task = nodes_mod.create_node(
        conn, project_id=project["id"], kind="task", title="t", status="in_progress", owner="claude",
    )
    rows = agent_status.agent_status(conn, project["id"], config)
    claude_row = next(r for r in rows if r["agent"] == "claude")
    assert claude_row["current"]["node_id"] == task["id"]
```

Confirm `core.spend`'s actual "record a spend" function name (grep `def.*spend` in `src/muvue/core/spend.py`) before finalizing the test — it may not be called `record_spend`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_status.py -v`
Expected: FAIL — `ModuleNotFoundError: muvue.core.agent_status`.

- [ ] **Step 3: Write `src/muvue/core/agent_status.py`**

```python
"""Per-agent status for the dashboard's Agents panel: which roles an
agent is routed to, what it's working on right now, and what it has
spent -- so "who is doing what" is answerable at a glance, without
reading the event log by hand."""

from __future__ import annotations

import sqlite3

from .config import MuvueConfig
from . import db as db_mod
from . import spend as spend_mod

ROLES = ("spec", "task", "subtask")


def agent_status(conn: sqlite3.Connection, project_id: int, config: MuvueConfig) -> list[dict]:
    spend_rows = spend_mod.project_spend(conn, project_id)
    out = []
    for name in sorted(config.agents):
        roles = [r for r in ROLES if getattr(config.routing, r) == name]
        current_row = db_mod.query_one(
            conn,
            "SELECT id, title, lease_until FROM nodes WHERE project_id = ? AND owner = ? "
            "AND status = 'in_progress' AND deleted_at IS NULL LIMIT 1",
            (project_id, name),
        )
        current = (
            {"node_id": current_row["id"], "title": current_row["title"], "lease_until": current_row["lease_until"]}
            if current_row is not None else None
        )
        spend = [
            {"unit": r["unit"], "spent": r["spent"]} for r in spend_rows if r["agent"] == name
        ]
        out.append({"agent": name, "roles": roles, "current": current, "spend": spend})
    return out
```

Add `agent_status` to `src/muvue/core/__init__.py`'s imports, matching the existing style.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_status.py -v`
Expected: PASS.

- [ ] **Step 5: Read what run-history data already exists for a node**

```bash
grep -n "node.started\|node.done\|node.failed\|attempts" src/muvue/core/nodes.py src/muvue/core/events.py | head -20
```

Determine whether attempts/runs are already queryable as structured rows (e.g. a table) or only as `events` rows that need filtering by `node_id` and `type_ IN (...)`. Write `GET /nodes/{id}/runs` to read from whichever already exists — do not create a new table for this; the design spec's "Runs section" is a read view over existing events/attempts data, not a new write path.

- [ ] **Step 6: Write the failing API tests**

```python
def test_agent_status_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.get(f"/agents/status?project_id={project_id}", headers=auth_headers)
    assert r.status_code == 200
    assert "agents" in r.json()
    assert any(a["agent"] == "fake" for a in r.json()["agents"])  # confirm the test config's configured agent name — likely "fake" per the `config` fixture; adjust if different


def test_node_runs_endpoint(client, ready_task, auth_headers):
    node_id = ready_task["task"]["id"]
    r = client.get(f"/nodes/{node_id}/runs", headers=auth_headers)
    assert r.status_code == 200
    assert "runs" in r.json()
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -k "agent_status or node_runs" -v`
Expected: FAIL with 404.

- [ ] **Step 8: Add the endpoints**

```python
    @app.get("/agents/status")
    def agents_status(request: Request, project_id: int) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                core.projects.get_project(conn, project_id)
                result = core.agent_status.agent_status(conn, project_id, config)
            except Exception as e:
                _handle_core_error(e)
        return {"agents": result}

    @app.get("/nodes/{node_id}/runs")
    def node_runs(request: Request, node_id: int) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                core.nodes.get_node(conn, node_id)  # 404s if the node doesn't exist
                rows = db_mod... # build from Step 5's finding: query events of the relevant run/attempt types for this node_id, ordered by id
            except Exception as e:
                _handle_core_error(e)
        return {"runs": rows}
```

Replace the `# build from Step 5's finding...` line with a real query built from what Step 5 discovered — this must not survive as a comment in the committed code. Reuse `_rows_to_list` (already defined at the top of `app.py`) to convert the query result.

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k "agent_status or node_runs" -v`
Expected: PASS.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/muvue/core/agent_status.py src/muvue/core/__init__.py src/muvue/api/app.py tests/
git commit -m "Add GET /agents/status and GET /nodes/{id}/runs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 11: `GET /graph` gains `carries` and resolved `agent`

**Files:**
- Modify: `src/muvue/api/app.py` (the `/graph` route, ~line 570)
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `GET /graph?project_id=N` — each node in the response gains `"agent": str` (the owner if set, else the routing-resolved agent for that node's kind); each edge gains `"carries": str | None`.

- [ ] **Step 1: Read the current `/graph` handler**

```bash
sed -n 570,595p src/muvue/api/app.py
```

- [ ] **Step 2: Write the failing test**

```python
def test_graph_endpoint_includes_carries_and_agent(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/spec", json={"title": "s", "body_md": "b"}, headers=auth_headers)
    spec_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "A"}, headers=auth_headers)
    a_id = r.json()["node"]["id"]
    r = client.post(f"/nodes/{spec_id}/children", json={"title": "B", "depends_on": [{"id": a_id, "carries": "audio"}]}, headers=auth_headers)

    r = client.get(f"/graph?project_id={project_id}", headers=auth_headers)
    body = r.json()
    node_a = next(n for n in body["nodes"] if n["id"] == a_id)
    assert "agent" in node_a
    edge = next(e for e in body["edges"] if e["from"] == a_id)
    assert edge["carries"] == "audio"
```

Adjust field names (`from`/`to` vs `node_id`/`depends_on`) to match whatever Step 1's read shows the current edge shape actually is.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -k graph_endpoint -v`
Expected: FAIL — `KeyError` or missing `carries`/`agent`.

- [ ] **Step 4: Add `carries` and `agent` to the response**

Modify the `/graph` handler's query/response building (found in Step 1) to: (a) select `carries` alongside the existing `deps` columns in whatever query builds edges, and include it in each edge dict; (b) for each node, compute `agent = node["owner"] or getattr(config.routing, node["kind"], None)` and include it in each node dict. Match the exact existing code structure (a dict comprehension, a loop, or `_rows_to_list` — read Step 1's output to see which) rather than rewriting the handler.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_api.py -k graph_endpoint -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/api/app.py tests/test_api.py
git commit -m "Add carries and resolved agent to GET /graph

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 12: docs, decisions, protocol version, CHANGELOG

**Files:**
- Modify: `docs/protocol.md`, `docs/decisions.md`, `docs/threat-model.md`, `CHANGELOG.md`
- Modify: wherever `protocol_version` is defined (find it: `grep -rn "protocol_version" src/muvue/`)

**Interfaces:** none (docs only).

- [ ] **Step 1: Bump `protocol_version`**

```bash
grep -rn "protocol_version\s*=" src/muvue/
```

Bump it by one from whatever its current value is (read the surrounding comment style and add a one-line note, matching how prior bumps were annotated — check `git log -p` on that line if the file itself has no history comment).

- [ ] **Step 2: Update `docs/protocol.md`**

Add a new `###` subsection (matching the file's existing prose style) listing: `POST /projects`, `POST /projects/{id}/spec`, `POST /nodes/{id}/children`, `POST /nodes/{id}/remove`, `POST /nodes/{id}/edit`, `POST /nodes/{id}/breakdown`, `POST /projects/{id}/run`, `GET /agents/status`, `GET /nodes/{id}/runs`, and the `carries`/`agent` additions to `GET /graph`. One or two sentences per endpoint, in the file's existing terse descriptive style (see the `GET /events` example already in the file).

- [ ] **Step 3: Add decisions**

Append four entries to `docs/decisions.md`, continuing the numbering from wherever it currently ends (167, 168, 169, 170 per this plan's research — confirm the actual next number first, since Tasks 1-11 may have landed other unrelated decisions in the meantime):

- One documenting human-verb parity: every CLI verb a person uses day to day now has a dashboard endpoint.
- One documenting that breakdown is spawned as a detached subprocess (`muvue _breakdown`), not driven by `core/runner.py`'s existing loop, and why (spec nodes are deliberately excluded from that loop).
- One documenting `deps.carries` and that a `dep.added` event (and its `rebuild` replay) were added, since neither existed before this plan.
- One documenting that `POST /projects` has no budget parameter, since no per-project budget field exists (budgets stay per-agent, in `config.toml`).

Each entry: one bold one-line title, then 3-8 sentences (what changed, why, what stays true), matching the exact format of entries #165/#166 already in the file.

- [ ] **Step 4: Update `docs/threat-model.md`**

Add one or two sentences noting that `/nodes/{id}/breakdown` and `/projects/{id}/run` spawn agent/runner subprocesses from the API, under the same controls already documented for `start?agent` (session auth, JSON-only, no query-string tokens) — find that existing paragraph and extend it, don't write a new section.

- [ ] **Step 5: Update `CHANGELOG.md`**

Add a `## [Unreleased]` section (or extend one if it already exists from other work) with an `### Added` list: the new endpoints, `deps.carries`, agent breakdown. Match the existing changelog's terse style (see the `[0.2.3]` entries already in the file).

- [ ] **Step 6: Run the full test suite one more time**

Run: `uv run pytest -q`
Expected: PASS (865+ tests, exact count will have grown from the earlier tasks).

- [ ] **Step 7: Commit**

```bash
git add docs/protocol.md docs/decisions.md docs/threat-model.md CHANGELOG.md src/muvue/
git commit -m "Document the project canvas backend: protocol, decisions, threat model

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

## Out of scope for this plan

- The frontend canvas itself (Preact components, flow layout, cards, agent chips) — a separate plan, written after this one is reviewed and merged, per the design spec's own "canvas is the page" work.
- `docs/protocol.md`'s MCP tool surface for `--carries` (the design spec mentions "the MCP tool gets the same field" — if muvue has an MCP server exposing `decompose`/`replan` as tools, grep `src/muvue/` for an `mcp` module and add `--carries` there too as a follow-up task once this plan's core `--carries` flag lands; not included here since Task 2's research didn't confirm the MCP tool's exact shape).
