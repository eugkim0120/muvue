# Mission Control Phase 1 (Sessions and Worktrees) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** muvue records every agent session in a repository (started by its runner or by hand), knows each worktree's branch, progress and stage, and shows both in a new Agents tab, with Stop for sessions muvue launched.

**Architecture:** Claude Code hooks (`muvue._hook`, stdlib only) and three new MCP tools append session lines to `.muvue/queue.jsonl`; `core.hooks.drain_queue` folds them into two new tables (`sessions`, `session_touches`, schema 9) through a new `core/sessions.py`, which also computes liveness on read. The runner inserts and ends its own `launched` rows and passes `MUVUE_SESSION` to the agent, so the agent's own session id becomes an alias of the run. `core/gitutil.py` gains `git worktree list` parsing and a sha-keyed `branch_status` cache, which `core/worktrees.py` turns into rows with a stage; three API routes serve both, and the dashboard gains project tabs with a working Agents tab.

**Tech Stack:** Python 3.11+, uv, pytest, FastAPI, SQLite; dashboard Preact + @preact/signals + Vite + vitest, built into the committed `src/muvue/api/static/index.html`.

**Spec:** `docs/superpowers/specs/2026-10-10-mission-control-design.md` (this plan covers "Phase 1: sessions and worktrees", its "Dashboard: Agents tab" section, and the Phase 1 rows of "Error handling" and "Testing").

**Where the work happens:** branch `feat/mission-control-p1`, in the worktree `/home/eugene/projects/muvue/.worktrees/mission-control-p1` (created at execution time with superpowers:using-git-worktrees; this plan does not create it). Every path below is relative to that worktree. Run Python commands from its root and npm commands from its `dashboard/`.

## Global Constraints

- No web fonts: the product keeps the system font stack in `dashboard/src/styles/tokens.css` (the mockups' IBM Plex is not shipped).
- The dashboard is one committed file, `src/muvue/api/static/index.html`, built by `cd dashboard && npm run build`; commit it whenever dashboard source changes. No network access except the daemon's own API; no CDN, no external script.
- `dashboard/src/api/routes.ts` is the only module that spells an API path; `tests/test_dashboard_static.py` checks every path in it is served, and `dashboard/test/no-stray-fetch.test.ts` checks no other module spells one.
- Every tappable control has a touch target of at least 44 px. The contrast and type-scale guard tests (`dashboard/test/contrast.test.ts`, `type-scale.test.ts`, `touch-targets.test.ts`) stay green; no `font-size` below 12 px.
- Every mutating request is a `POST` with `Content-Type: application/json`, under the existing auth (`_require_session`) and Origin/Host rules in `src/muvue/api/app.py`.
- The hook hot path stays stdlib only (`sys`, `os`, `json`, `time` at module scope, `tests/test_hook_import_graph.py`). `PreToolUse` capture is one `O_APPEND` write: no SQLite and no subprocess on that path.
- Schema version becomes 9 (`src/muvue/core/schema.py`). New tables need only `CREATE TABLE IF NOT EXISTS`; `run_migrate` bumps the version.
- Liveness thresholds live in a new `[sessions]` config table: `working_s = 120`, `idle_s = 900`, `ended_s = 7200` (seconds).
- Hand-started sessions are view-only: muvue never sends them messages and never stops them. `POST /sessions/{id}/stop` on a hand session returns 409 with the detail `started by hand; stop it where it runs`.
- A hook never fails the agent: any error writes a `hook_error` queue line and exits 0. A line for a session whose repository cannot be found is dropped by the hook (not queued).
- `git` failures in `list_worktrees` or `branch_status` are shown on the affected row as `git failed: <first line of stderr>`, never hidden and never fatal to the page.
- `uv run pytest -q` stays green and coverage stays at or above 85% (`uv run pytest -q --cov=muvue.core`).
- Every commit message ends with exactly these two lines:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
  ```

## Spec deviations

Each item is a place where the spec, applied literally, is wrong or impossible against the code. The plan does what is written here instead.

1. **`hook_error` does not exist today.** The spec says "any error writes a `hook_error` queue line and exits 0, as today". `grep -rn hook_error src tests` finds nothing; today an exception in `run` fails open silently (`src/muvue/_hook.py:437-439`) or escapes `main`. Task 2 adds the `hook_error` line for the Claude Code hooks.
2. **The adapter is named `claude-code`, not `claude`** (`src/muvue/cli/main.py:499`). The command is `muvue adapter install claude-code --user`.
3. **User-level hooks only record; they never allow or deny.** "The same hook commands" in `~/.claude/settings.json` would apply PreToolUse's Edit/Write block ("no active muvue node", `src/muvue/_hook.py:259-263`) to every registered repository, including ones that never installed the adapter. The user-level commands carry a `MUVUE_HOOK_SCOPE=user ` prefix: such a hook records the line and exits 0, and records nothing when the project's own `.claude/settings.json` already runs muvue's hooks (otherwise both installs would spool every event twice, since the commands differ).
4. **The `.git` file walk is a fallback for Claude Code hooks only, and those hooks only record there.** The spec puts it inside `_find_repo_root` (`src/muvue/_hook.py:82-91`), which git hooks share. A `post-commit` in a linked worktree would then spool `_read_head_sha(repo_root)`, the main checkout's HEAD (`src/muvue/_hook.py:127-164`, `:664-667`), not the commit just made; decision #153 links worktree commits at `done` instead (`docs/protocol.md`, "Commits made in a node's worktree ... spool nothing"). And allow/deny in an outside worktree would read the main checkout's `.muvue/current_node` (`src/muvue/_hook.py:100-111`), the wrong session's node. Today these hooks exit 0 at once (`src/muvue/_hook.py:650-656`); recording without deciding keeps that behaviour and adds capture.
5. **Strict-mode worktrees point into the airlock, not the repository.** `bind_worktree` runs `git --git-dir <airlock> worktree add` (`src/muvue/core/strict.py:154-156`), so the worktree's `gitdir:` is `~/.muvue/airlocks/<hash>.git/worktrees/node-<id>`. The hook reads the airlock's `muvue-repo-root` marker (`src/muvue/core/strict.py:91`) to find the repository. For the same reason `GET /worktrees` also lists the airlock's worktrees (`git worktree list` in the checkout does not show them), with base branch `main`.
6. **Liveness has a gap.** The table defines working (event within `working_s`) and idle (no event for more than `idle_s`) but nothing for a session that is not waiting and last reported between 120 s and 900 s ago (a long test run, for example). The plan adds a fifth state, `quiet`, for that window. Precedence: ended, then waiting, then working, then quiet, then idle.
7. **PreToolUse lines and MCP heartbeats do not become `hook.*` events.** One audit row per tool call would flood `events`. They update `sessions`/`session_touches` only; every other session line still writes its `hook.<event>` audit event, in the same transaction as the session upsert.
8. **The PreToolUse matcher stays `Edit|Write|Bash`** (`src/muvue/core/adapters.py:111-112`). Widening it would spawn a Python process on every Read and Grep. So only those three tools refresh liveness; a session reading for a long time shows `quiet`.
9. **Session fields are added only when the payload has a `session_id`, and `pre-tool-use` writes a line only then.** This keeps the existing golden line (`tests/test_hook_fast_path.py:73-81`) and the "within deadline does not spool" test (`:242-254`) true for payloads without a session.
10. **The hook line's `node_id` never binds a session.** It is still resolved from `.muvue/current_node` for the decision hooks and the audit event. The MCP `session_start(node_id)` argument travels as `bind_node_id` and is used only after the worktree rule and the launched rule.
11. **The driver's pid does not exist before the driver is spawned.** The runner inserts the `launched` row before invoking the driver with `pid` NULL; `invoke_driver` gains `env` and `on_spawn(pid)`, and the row's pid is set from `on_spawn`. The test fake at `tests/test_runner_live_findings.py:52` takes `*, log_path=None` only, so it gains `**kw`.
12. **No event marks checks as running.** `done` runs the checks inline (`src/muvue/core/nodes.py`, called from `src/muvue/core/runner.py` after the driver returns). The `checks` stage is therefore inferred: the node is `in_progress` and its latest launched session has ended (the runner is past the agent and inside `done`). Hand sessions never show `checks`.
13. **`branch_status` takes shas, not a branch name:** `branch_status(repo_root, *, base_sha, tip_sha)`. The cache key is (repository, tip sha, base sha), so a cached call spawns no git. Each refresh still runs one `git worktree list` and one `git rev-parse <base>` per repository.
14. **"Merged" is a heuristic.** git cannot tell a branch merged by fast-forward from a branch created at an older base commit with nothing on it. A row is `merged` when it has no commits ahead and either the base moved past it (`behind > 0`) or its node is `done`.
15. **Launched spend is per node and agent.** `node_usage` has no session column, so a launched session shows the sums of `node_usage` for its node and agent; several runs of one node add up.
16. **`protocol_version` goes from 3 to 4** (the spec is silent). New hook events, new MCP tools and new endpoints are a verb change under `docs/protocol.md` ("Bump on any verb change"); the bump also makes `doctor` tell existing installs to re-run `muvue adapter install`, which is how they get `UserPromptSubmit` and `SessionEnd`.
17. **Tabs live in the query (`#/p/<id>?tab=agents`), not the path.** `#/plan` is a legacy route that redirects to the canvas (`dashboard/src/router.ts:44-46`, `dashboard/src/app.tsx:20-28`).
18. **Side panel buttons.** "Open session" expands the session's details in the panel (id, directory, transcript path, files touched, spend); there is nothing else to open for a hand session. "Open diff" opens the node's sheet, which already has the diff; it is hidden for off-plan rows, whose changed files the panel lists.
19. **`GET /sessions?project_id=N` also returns off-plan sessions** (`project_id` NULL): a session with no node belongs to the repository, not to any one project.
20. **Sessions are not replayed.** `core/rebuild.py` replays decisions; sessions are observations of outside processes, like `lease_until`. They stay out of `ALL_REPLAYED_TABLES`.

## File structure

| File | Responsibility |
|---|---|
| `src/muvue/core/schema.py` (modify) | `sessions`, `session_touches`; `SCHEMA_VERSION = 9` |
| `src/muvue/core/migrate.py` (modify) | comment for 8 -> 9 (tables come from `init_db`) |
| `src/muvue/core/config.py` (modify) | `SessionsConfig`, `[sessions]` in the default TOML; later `PROTOCOL_VERSION = 4` |
| `src/muvue/_hook.py` (modify) | session fields on queue lines, `user-prompt-submit`/`session-end`, PreToolUse line, worktree walk, user scope, `hook_error` |
| `src/muvue/cli/main.py` (modify) | `muvue hook` accepts the new events; `adapter install --user` |
| `src/muvue/core/adapters.py` (modify) | six Claude Code events, user-level install, install-scope report |
| `src/muvue/core/doctor.py` (modify) | reports which Claude Code install is active; warns on a stale repo install |
| `src/muvue/core/sessions.py` (create) | everything about sessions: folding queue lines, liveness, listing, launched rows, MCP spooling, stop |
| `src/muvue/core/hooks.py` (modify) | drain calls `sessions.apply_line` |
| `src/muvue/core/drivers.py` (modify) | `invoke_driver(env=, on_spawn=)` |
| `src/muvue/core/runner.py` (modify) | launched session around the driver call |
| `src/muvue/mcp_server.py` (modify) | `session_start`, `session_heartbeat`, `session_end` |
| `src/muvue/core/gitutil.py` (modify) | `worktree_top`, `branch_at` (no subprocess); `list_worktrees`, `rev_parse`, `branch_status` + cache |
| `src/muvue/core/worktrees.py` (create) | worktree rows for the Agents tab: base, status, stage, node, planned files, sessions |
| `src/muvue/core/__init__.py` (modify) | export `sessions`, `worktrees` |
| `src/muvue/api/app.py` (modify) | `GET /sessions`, `GET /worktrees`, `POST /sessions/{id}/stop` |
| `dashboard/src/api/routes.ts` (modify) | the three paths |
| `dashboard/src/router.ts` (modify) | `ProjectTab`, `projectTabOf`, `setProjectTab` |
| `dashboard/src/project/ProjectTabs.tsx` (create) | the four tabs and the placeholder |
| `dashboard/src/app.tsx` (modify) | renders the selected tab |
| `dashboard/src/agents/agentsData.ts` (create) | types and pure functions for the Agents tab |
| `dashboard/src/agents/AgentsTab.tsx` (create) | Branches, "Worktrees with no agent", git graph, stage bar |
| `dashboard/src/agents/BranchPanel.tsx` (create) | side panel with Open session / Open diff / Stop agent |
| `dashboard/src/agents/agents.css` (create), `dashboard/src/shell/shell.css` (modify), `dashboard/src/main.tsx` (modify) | styles |
| `dashboard/scripts/ui-check.mjs` (modify) | Agents tab at 390 and 1280 wide |
| `README.md`, `docs/decisions.md`, `docs/protocol.md`, `CHANGELOG.md` (modify) | docs |

Task order follows dependencies: 1 (schema, config) -> 2 (hook) -> 3 (adapter) -> 4 (drain) -> 5 (liveness) -> 6 (runner) -> 7 (MCP) -> 8 (worktrees) -> 9 (API) -> 10 (tab shell) -> 11 (Agents tab) -> 12 (protocol, docs, end-to-end checks).

---

### Task 1: Schema version 9 and the `[sessions]` config table

**Files:**
- Modify: `src/muvue/core/schema.py:34-40` (docstring, version), `:320-324` (tables)
- Modify: `src/muvue/core/migrate.py:99-100`
- Modify: `src/muvue/core/config.py:9`, `:58-70`, `:141-154`, `:205-208`
- Modify: `tests/test_schema_v4.py:28-29`
- Create: `tests/test_sessions_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `sessions(id, project_id, node_id, agent, origin, cwd, branch, transcript_path, pid, started_at, last_event_at, last_action, ended_at, waiting, alias_of)` and `session_touches(session_id, path, at)`; `muvue.core.config.SessionsConfig` with `working_s: int = 120`, `idle_s: int = 900`, `ended_s: int = 7200`; `MuvueConfig.sessions: SessionsConfig`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_schema_v4.py:28-29`:

```python
def test_schema_version_is_9():
    assert SCHEMA_VERSION == 9
```

Create `tests/test_sessions_schema.py`:

```python
"""Mission control phase 1: the `sessions` and `session_touches` tables
(schema version 9) and the `[sessions]` liveness thresholds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from muvue.core import db as core_db, migrate
from muvue.core.config import DEFAULT_CONFIG_TOML, ConfigError, SessionsConfig, load_config

SESSION_COLUMNS = {
    "id", "project_id", "node_id", "agent", "origin", "cwd", "branch", "transcript_path",
    "pid", "started_at", "last_event_at", "last_action", "ended_at", "waiting", "alias_of",
}


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_new_database_has_the_sessions_tables(tmp_path: Path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    try:
        assert {"sessions", "session_touches"} <= _tables(conn)
        assert {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")} == SESSION_COLUMNS
        assert {r["name"] for r in conn.execute("PRAGMA table_info(session_touches)")} == {"session_id", "path", "at"}
    finally:
        conn.close()


def test_origin_is_launched_or_hand(tmp_path: Path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (id, agent, origin, cwd, started_at, last_event_at) "
                "VALUES ('s', 'claude', 'robot', '/x', 't', 't')"
            )
    finally:
        conn.close()


def test_migrate_from_version_8_creates_the_sessions_tables(tmp_path: Path):
    db_path = tmp_path / ".muvue" / "muvue.db"
    db_path.parent.mkdir()
    conn = core_db.init_db(db_path)
    conn.execute("DROP TABLE session_touches")
    conn.execute("DROP TABLE sessions")
    conn.execute("UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    assert migrate.run_migrate(tmp_path) == 9

    conn = core_db.connect(db_path)
    try:
        assert {"sessions", "session_touches"} <= _tables(conn)
    finally:
        conn.close()


def test_the_default_config_has_the_session_thresholds(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(path)
    assert (cfg.sessions.working_s, cfg.sessions.idle_s, cfg.sessions.ended_s) == (120, 900, 7200)
    assert cfg.sessions == SessionsConfig()


def test_thresholds_out_of_order_are_rejected(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML.replace("idle_s = 900", "idle_s = 60"))
    with pytest.raises(ConfigError, match="working_s < idle_s < ended_s"):
        load_config(path)


def test_an_unknown_sessions_key_is_rejected(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML.replace("ended_s = 7200", "ended_s = 7200\nstale_s = 5"))
    with pytest.raises(ConfigError, match="stale_s"):
        load_config(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sessions_schema.py tests/test_schema_v4.py -q`
Expected: FAIL. `ImportError: cannot import name 'SessionsConfig'` for the new file, and `assert 8 == 9` in `test_schema_version_is_9`.

- [ ] **Step 3: Add the tables and bump the version**

In `src/muvue/core/schema.py`, append to the version-history docstring (after the `7 -> 8` bullet, before the closing `"""`):

```
- SCHEMA_VERSION 8 -> 9 (mission control phase 1): new `sessions` table
  (one row per run of an agent CLI, launched by the runner or started by
  hand) and `session_touches` (repository-relative paths a session
  edited). New tables only, so `core.migrate.run_migrate` needs no
  `ALTER TABLE` step. Sessions are observations, not decisions:
  `core.rebuild` does not replay them.
```

Change `SCHEMA_VERSION = 8` to `SCHEMA_VERSION = 9`.

Insert before `CREATE TABLE IF NOT EXISTS schema_meta (` in `SCHEMA_SQL`:

```sql
-- Mission control phase 1 (schema version 9): every agent session in
-- this repository. Written by the runner for `launched` rows and by the
-- queue drain (core.sessions.apply_line) for everything else; liveness
-- is computed on read from last_event_at, waiting and ended_at.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,            -- the agent's own session id; runner-made: "run-<uuid>"
    project_id INTEGER REFERENCES projects(id),
    node_id INTEGER REFERENCES nodes(id),   -- NULL = off-plan
    agent TEXT NOT NULL,            -- "claude", or an [agents.*] name
    origin TEXT NOT NULL CHECK (origin IN ('launched', 'hand')),
    cwd TEXT NOT NULL,
    branch TEXT,
    transcript_path TEXT,
    pid INTEGER,                    -- launched sessions only
    started_at TEXT NOT NULL,
    last_event_at TEXT NOT NULL,
    last_action TEXT,               -- "Edit web/tracks.ts", "turn ended", ...
    ended_at TEXT,
    waiting INTEGER NOT NULL DEFAULT 0,  -- 1 after Stop, 0 after UserPromptSubmit/PreToolUse
    alias_of TEXT REFERENCES sessions(id)  -- agent session id folded into a launched row
);

CREATE TABLE IF NOT EXISTS session_touches (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    path TEXT NOT NULL,             -- repository-relative
    at TEXT NOT NULL,
    PRIMARY KEY (session_id, path)
);

```

In `src/muvue/core/migrate.py`, after the `deps.carries` line (`:100`), add:

```python
            # SCHEMA_VERSION 8 -> 9 (mission control phase 1): `sessions`
            # and `session_touches` are new tables, already created by
            # `init_db`'s `CREATE TABLE IF NOT EXISTS` re-apply above.
```

- [ ] **Step 4: Add `SessionsConfig`**

In `src/muvue/core/config.py`, change the pydantic import to:

```python
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
```

Add after `class BudgetConfig` (after `:70`):

```python
class SessionsConfig(StrictModel):
    """`[sessions]` (mission control phase 1): liveness thresholds, in
    seconds since a session's last event. `core.sessions.session_state`
    reads them: working within `working_s`, idle after `idle_s`, ended
    after `ended_s`; "quiet" in between working and idle."""

    working_s: int = 120
    idle_s: int = 900
    ended_s: int = 7200

    @model_validator(mode="after")
    def _ordered(self) -> "SessionsConfig":
        if not 0 < self.working_s < self.idle_s < self.ended_s:
            raise ValueError(
                "[sessions] needs 0 < working_s < idle_s < ended_s "
                f"(got {self.working_s}, {self.idle_s}, {self.ended_s})"
            )
        return self
```

In `class MuvueConfig`, after `budget: BudgetConfig = Field(default_factory=BudgetConfig)`, add:

```python
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
```

In `DEFAULT_CONFIG_TOML`, after the `[budget]` block (after `max_nodes_per_run = 20`), add:

```toml

[sessions]
working_s = 120
idle_s = 900
ended_s = 7200
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sessions_schema.py tests/test_schema_v4.py tests/test_migrate.py tests/test_migrate_guard.py tests/test_config.py tests/test_doctor_schema_version.py -q`
Expected: PASS, no failures.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (about 830 tests). `tests/test_protocol_version.py` is untouched by this task and still passes.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/schema.py src/muvue/core/migrate.py src/muvue/core/config.py tests/test_schema_v4.py tests/test_sessions_schema.py
git commit -F - <<'MSG'
Add the sessions tables (schema 9) and the [sessions] config table

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 2: Hooks capture sessions

**Files:**
- Modify: `src/muvue/_hook.py:1-41` (docstring), `:59-68` (constants), `:82-91` (new walk after it), `:396-444` (`run`), `:635-679` (`main`)
- Modify: `src/muvue/cli/main.py:589` (`muvue hook` accepts the new events)
- Create: `tests/test_hook_sessions.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the hook never touches the DB for capture).
- Produces, in `muvue._hook`:
  - `CAPTURE_HOOKS = ("user-prompt-submit", "session-end")`, `SESSION_HOOKS = DECISION_HOOKS + CAPTURE_HOOKS`
  - `HOOK_SCOPE_ENV = "MUVUE_HOOK_SCOPE"` (value `"user"` for the user-level install), `LAUNCHED_SESSION_ENV = "MUVUE_SESSION"`
  - `record(name: str, repo_root: str, payload: dict) -> None`: appends the event's queue line
  - `_find_worktree_repo_root(start: str) -> str | None`
  - `_append_queue(repo_root: str, event: dict) -> None` and `_now_iso() -> str` (existing; Task 7 calls them)
  - Queue line shape (golden): `{"event", "ts", "node_id", [event extras], "session_id", "cwd", "transcript_path", "agent": "claude", ["launched_session"]}`. Extras: `pre-tool-use` -> `tool_name`, and `file_path` for Edit/Write; `pre-compact` -> `summary`; `session-end` -> `reason`. `hook_error` line: `{"event": "hook_error", "ts", "hook", "error": "<Type>: <message>"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_sessions.py`:

```python
"""Mission control phase 1: what the Claude Code hooks spool for
sessions. Golden queue lines for every event, the PreToolUse line, the
two record-only events, a linked worktree outside the repository, the
user-level install, and `hook_error`."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import adapters
from muvue.core.repo_init import init_repo

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
SESSION = {"session_id": "s-1", "cwd": "/work/app", "transcript_path": "/t/s-1.jsonl"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # This suite may itself run inside a Claude Code or muvue-launched
    # session; none of that may leak into the lines under test.
    for name in ("MUVUE_SESSION", "MUVUE_HOOK_SCOPE", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    init_repo(root)
    return root


def _queue_lines(repo_root: Path) -> list[dict]:
    path = repo_root / ".muvue" / "queue.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _main(monkeypatch, name: str, path: Path, payload: dict) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    return _hook.main([name, str(path)])


# -- golden lines ----------------------------------------------------------


def test_a_stop_line_carries_the_session(repo):
    _hook.run("stop", str(repo), {**SESSION, "stop_hook_active": True})
    lines = _queue_lines(repo)
    assert lines == [{"event": "stop", "ts": lines[0]["ts"], "node_id": None, **SESSION, "agent": "claude"}]


def test_pre_tool_use_appends_one_line_per_call_with_the_edited_file(repo):
    _hook.run("pre-tool-use", str(repo), {**SESSION, "tool_name": "Edit", "tool_input": {"file_path": "/work/app/a.py", "old_string": "x"}})
    _hook.run("pre-tool-use", str(repo), {**SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}})
    lines = _queue_lines(repo)
    assert lines == [
        {"event": "pre-tool-use", "ts": lines[0]["ts"], "node_id": None, "tool_name": "Edit",
         "file_path": "/work/app/a.py", **SESSION, "agent": "claude"},
        {"event": "pre-tool-use", "ts": lines[1]["ts"], "node_id": None, "tool_name": "Bash",
         **SESSION, "agent": "claude"},
    ]


def test_pre_tool_use_without_a_session_spools_nothing(repo):
    _hook.run("pre-tool-use", str(repo), {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert _queue_lines(repo) == []


def test_pre_compact_keeps_its_summary(repo):
    _hook.run("pre-compact", str(repo), {**SESSION, "summary": "did things"})
    lines = _queue_lines(repo)
    assert lines == [{"event": "pre-compact", "ts": lines[0]["ts"], "node_id": None, "summary": "did things",
                      **SESSION, "agent": "claude"}]


@pytest.mark.parametrize("name, extra, expected", [
    ("user-prompt-submit", {"prompt": "do it"}, {}),
    ("session-end", {"reason": "logout"}, {"reason": "logout"}),
])
def test_record_only_events_spool_and_allow(repo, name, extra, expected):
    outcome = _hook.run(name, str(repo), {**SESSION, **extra})
    assert (outcome.exit_code, outcome.stdout, outcome.stderr) == (0, "", "")
    lines = _queue_lines(repo)
    # The prompt text itself is never spooled.
    assert lines == [{"event": name, "ts": lines[0]["ts"], "node_id": None, **SESSION, "agent": "claude", **expected}]


def test_a_launched_agent_names_its_run(repo, monkeypatch):
    monkeypatch.setenv("MUVUE_SESSION", "run-abc")
    _hook.run("session-start", str(repo), SESSION)
    assert _queue_lines(repo)[0]["launched_session"] == "run-abc"


# -- finding the repository from a worktree --------------------------------


def test_a_hook_in_a_linked_worktree_records_into_the_owning_repo_and_never_blocks(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "feat", str(wt)], cwd=root, check=True, capture_output=True)

    payload = {**SESSION, "cwd": str(wt), "tool_name": "Edit", "tool_input": {"file_path": str(wt / "a.py")}}
    # In the main checkout an Edit with no active node is blocked (exit 2);
    # outside it the hook only records.
    assert _main(monkeypatch, "pre-tool-use", wt, payload) == 0
    assert [line["event"] for line in _queue_lines(root)] == ["pre-tool-use"]
    assert not (wt / ".muvue").exists()


def test_a_strict_mode_worktree_resolves_through_the_airlock_marker(tmp_path):
    root = tmp_path / "repo"
    (root / ".muvue").mkdir(parents=True)
    airlock = tmp_path / "airlock.git"
    (airlock / "worktrees" / "node-3").mkdir(parents=True)
    (airlock / "muvue-repo-root").write_text(str(root))
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {airlock / 'worktrees' / 'node-3'}\n")
    assert _hook._find_worktree_repo_root(str(wt / "src")) == str(root)


def test_a_worktree_of_an_uninitialised_repo_is_ignored(tmp_path):
    root = tmp_path / "plain"
    (root / ".git" / "worktrees" / "x").mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {root / '.git' / 'worktrees' / 'x'}\n")
    assert _hook._find_worktree_repo_root(str(wt)) is None


# -- user-level install ----------------------------------------------------


def test_user_scope_hooks_only_record(repo, monkeypatch):
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    payload = {**SESSION, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "a.py")}}
    assert _main(monkeypatch, "pre-tool-use", repo, payload) == 0
    assert [line["event"] for line in _queue_lines(repo)] == ["pre-tool-use"]


def test_user_scope_defers_to_the_projects_own_hooks(repo, monkeypatch):
    adapters.install_claude_code(repo, protocol_version=3)
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    assert _main(monkeypatch, "stop", repo, SESSION) == 0
    assert _queue_lines(repo) == []


def test_user_scope_outside_a_registered_repo_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _main(monkeypatch, "session-start", plain, SESSION) == 0
    assert not (plain / ".muvue").exists()


# -- errors -----------------------------------------------------------------


def test_a_crashing_hook_spools_hook_error_and_lets_the_agent_through(repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_hook, "run", boom)
    assert _main(monkeypatch, "stop", repo, SESSION) == 0
    lines = _queue_lines(repo)
    assert lines == [{"event": "hook_error", "ts": lines[0]["ts"], "hook": "stop", "error": "RuntimeError: boom"}]


# -- hot path ---------------------------------------------------------------


def test_pre_tool_use_capture_opens_no_sqlite_and_no_subprocess(repo):
    payload = json.dumps({**SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}})
    code = (
        "import io, json, sys\n"
        f"sys.stdin = io.StringIO({payload!r})\n"
        "import muvue._hook as h\n"
        f"rc = h.main(['pre-tool-use', {str(repo)!r}])\n"
        "print(json.dumps({'exit': rc, 'sqlite3': 'sqlite3' in sys.modules, "
        "'subprocess': 'subprocess' in sys.modules}))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUVUE_", "CLAUDE_"))}
    env["PYTHONPATH"] = str(SRC_DIR)
    out = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"exit": 0, "sqlite3": False, "subprocess": False}
    assert [line["event"] for line in _queue_lines(repo)] == ["pre-tool-use"]


def test_the_cli_hook_command_accepts_the_new_events(repo):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUVUE_", "CLAUDE_"))}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "hook", "user-prompt-submit", str(repo)],
        input=json.dumps(SESSION), capture_output=True, text=True, cwd=repo, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert _queue_lines(repo)[-1]["event"] == "user-prompt-submit"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hook_sessions.py -q`
Expected: FAIL. Most tests fail on the line shape (no `session_id`/`agent` in the stop line, no pre-tool-use line); `_find_worktree_repo_root` raises `AttributeError`; the CLI test exits 0 but spools nothing (`IndexError`).

- [ ] **Step 3: Add the constants and the worktree walk to `src/muvue/_hook.py`**

Replace `DECISION_HOOKS = ("pre-tool-use", "session-start", "stop", "pre-compact")` (`:68`) with:

```python
DECISION_HOOKS = ("pre-tool-use", "session-start", "stop", "pre-compact")
# Claude Code events muvue only records, never decides on (mission
# control phase 1): a prompt means the session is working again, and
# SessionEnd closes it.
CAPTURE_HOOKS = ("user-prompt-submit", "session-end")
SESSION_HOOKS = DECISION_HOOKS + CAPTURE_HOOKS
_FILE_TOOL_NAMES = ("Edit", "Write")
# `muvue adapter install claude-code --user` prefixes its commands with
# `MUVUE_HOOK_SCOPE=user`; such a hook only records (see `_session_hook`).
HOOK_SCOPE_ENV = "MUVUE_HOOK_SCOPE"
# `core.runner.run_node` sets this in a launched agent's environment; the
# agent's hooks copy it into every line as `launched_session`.
LAUNCHED_SESSION_ENV = "MUVUE_SESSION"
```

After `_find_repo_root` (`:82-91`), add:

```python
def _repo_from_gitfile(gitfile: str) -> str | None:
    """`<worktree>/.git` in a linked worktree is a file holding `gitdir:
    <common>/worktrees/<name>`. `<common>` is `<repo>/.git` for a worktree
    of the user's checkout, or the strict-mode airlock, whose marker file
    names the real repository (`core.strict.REPO_ROOT_MARKER`). A string
    parse and two small reads: no `git` subprocess on the hot path."""
    try:
        with open(gitfile) as f:
            first = f.readline().strip()
    except OSError:
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = first[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(os.path.dirname(gitfile), gitdir)
    worktrees_dir = os.path.dirname(os.path.normpath(gitdir))
    if os.path.basename(worktrees_dir) != "worktrees":
        return None
    common = os.path.dirname(worktrees_dir)
    try:
        with open(os.path.join(common, AIRLOCK_REPO_ROOT_MARKER)) as f:
            return f.read().strip() or None
    except OSError:
        pass
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return None


def _find_worktree_repo_root(start: str) -> str | None:
    """For a Claude Code hook in a linked worktree outside the repository
    (muvue's own under `~/.muvue/worktrees/`, or one made by hand), where
    `_find_repo_root` finds no `.muvue/`: the repository that owns the
    worktree, if it has `.muvue/`. Git hooks never use this (see
    `main`)."""
    cur = os.path.abspath(start)
    while True:
        dotgit = os.path.join(cur, ".git")
        if os.path.isfile(dotgit):
            repo = _repo_from_gitfile(dotgit)
            return repo if repo and os.path.isdir(os.path.join(repo, ".muvue")) else None
        if os.path.isdir(dotgit):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
```

- [ ] **Step 4: Add the session fields and `record`**

After `_resolve_node_id` (`:220-222`), add:

```python
def _session_fields(payload: dict) -> dict:
    """What every line of a Claude Code session carries (mission control
    phase 1). Empty when the payload names no session, so a payload
    without one spools exactly what it did before."""
    if not isinstance(payload.get("session_id"), str):
        return {}
    fields = {key: payload[key] for key in ("session_id", "cwd", "transcript_path") if isinstance(payload.get(key), str)}
    fields["agent"] = "claude"
    launched = os.environ.get(LAUNCHED_SESSION_ENV)
    if launched:
        fields["launched_session"] = launched
    return fields


def _event_line(name: str, repo_root: str, payload: dict) -> dict | None:
    """The queue line for one Claude Code hook event, or None for a
    PreToolUse call outside a session: PreToolUse fires on every tool
    call, so it writes a line only when there is a session to update."""
    session = _session_fields(payload)
    if name == "pre-tool-use" and not session:
        return None
    event = {"event": name, "ts": _now_iso(), "node_id": _resolve_node_id(repo_root, payload)}
    if name == "pre-compact":
        event["summary"] = payload.get("summary")
    elif name == "pre-tool-use":
        tool_name = payload.get("tool_name")
        event["tool_name"] = tool_name if isinstance(tool_name, str) else ""
        tool_input = payload.get("tool_input")
        if tool_name in _FILE_TOOL_NAMES and isinstance(tool_input, dict) and isinstance(tool_input.get("file_path"), str):
            event["file_path"] = tool_input["file_path"]
    elif name == "session-end" and isinstance(payload.get("reason"), str):
        event["reason"] = payload["reason"]
    event.update(session)
    return event


def record(name: str, repo_root: str, payload: dict) -> None:
    """Spool one Claude Code hook event: a single `O_APPEND` write, no DB
    and no subprocess. `core.hooks.drain_queue` folds it into
    `sessions` later."""
    line = _event_line(name, repo_root, payload)
    if line is not None:
        _append_queue(repo_root, line)


def _spool_hook_error(repo_root: str, hook: str, error: BaseException) -> None:
    _append_queue(
        repo_root,
        {"event": "hook_error", "ts": _now_iso(), "hook": hook, "error": f"{type(error).__name__}: {error}"},
    )


def _project_has_muvue_hooks(project_dir: str) -> bool:
    """Whether the project's own `.claude/settings.json` runs muvue's
    hooks, in which case Claude Code already runs them for this session
    and a user-level hook must not spool the event a second time."""
    path = os.path.abspath(os.path.join(project_dir, ".claude", "settings.json"))
    if path == os.path.abspath(os.path.expanduser(os.path.join("~", ".claude", "settings.json"))):
        return False
    try:
        with open(path) as f:
            return "muvue._hook" in f.read()
    except OSError:
        return False
```

- [ ] **Step 5: Spool from `run` and route session hooks through one function**

In `run` (`:396-444`), replace the docstring and everything from `if name == "pre-tool-use":` through `_append_queue(repo_root, event)` with:

```python
    """One Claude Code hook, end to end: spool its line (`record`), then
    decide under the deadline. `user-prompt-submit` and `session-end`
    only record."""
    record(name, repo_root, payload)
    if name == "pre-tool-use":
        result = pre_tool_use(repo_root, payload, clock=clock, deadline_s=deadline_s)
        if result.get("decision") == "block":
            return HookOutcome(2, "", result.get("reason", "") + "\n")
        return HookOutcome()
    if name in CAPTURE_HOOKS:
        return HookOutcome()

    start = clock()
    node_id = _resolve_node_id(repo_root, payload)
```

(The rest of `run`, from `if name == "stop" and payload.get("stop_hook_active"):` on, is unchanged.)

Add before `def main(`:

```python
def _session_hook(name: str, repo_root: str, path: str, *, observe_only: bool) -> int:
    """One Claude Code hook from `main`. A hook from the user-level install
    (`MUVUE_HOOK_SCOPE=user`) only records, and records nothing when the
    project's own settings already run muvue's hooks. A hook in a linked
    worktree outside the repository (`observe_only`) only records too:
    `.muvue/current_node` belongs to the main checkout, so its allow/deny
    would be about another session. Any error is spooled as `hook_error`
    and the agent is let through (exit 0)."""
    payload = _read_stdin_json()
    user_scope = os.environ.get(HOOK_SCOPE_ENV) == "user"
    if user_scope and _project_has_muvue_hooks(os.environ.get("CLAUDE_PROJECT_DIR") or os.path.abspath(path)):
        return 0
    try:
        if observe_only or user_scope:
            record(name, repo_root, payload)
            return 0
        outcome = run(name, repo_root, payload)
    except Exception as e:
        try:
            _spool_hook_error(repo_root, name, e)
        except OSError as spool_error:
            sys.stderr.write(f"muvue: {name} hook failed ({e!r}) and could not spool it ({spool_error!r})\n")
        return 0
    sys.stdout.write(outcome.stdout)
    sys.stderr.write(outcome.stderr)
    return outcome.exit_code
```

In `main`, replace from `repo_root = _find_repo_root(path)` through the end of the `if name in DECISION_HOOKS:` block (`:650-662`) with:

```python
    repo_root = _find_repo_root(path)
    in_linked_worktree = False
    if repo_root is None and name in SESSION_HOOKS:
        # A Claude Code session in a worktree outside the repository.
        # Git hooks never take this path: see `_find_worktree_repo_root`.
        repo_root = _find_worktree_repo_root(path)
        in_linked_worktree = repo_root is not None
    if repo_root is None:
        # No .muvue/ found -- nothing to spool against, nothing to
        # decide. Fail open silently rather than raise: a hook shim
        # left behind in a repo that later ran `muvue uninit` must not
        # break every tool call.
        return 0

    if name in SESSION_HOOKS:
        return _session_hook(name, repo_root, path, observe_only=in_linked_worktree)
```

- [ ] **Step 6: Update the module docstring**

In the module docstring, replace the paragraph starting `Every hook event except \`PreToolUse\` does the minimum possible amount` (`:18-27`) with:

```
Every hook event does the minimum possible amount of work on its way
in: append one JSON line describing the event to `.muvue/queue.jsonl`
(gitignored, append-only spool). `PreToolUse` writes its line only when
the payload names a Claude Code session (mission control phase 1), so a
tool call costs one `O_APPEND` write and no DB open unless it is an
Edit/Write decision. All the real processing (parsing commit trailers,
linking commits, folding session lines into `sessions`, ...) happens
later, when something drains the queue --
`core.hooks.drain_queue`, a normal `muvue.core` module that runs
through `write_txn` as usual (working rule 3: the queue *append* here
is intentionally NOT a core DB write, by design, to stay off SQLite on
the hot path; the *drain* is a core write like any other mutation).

Session lines carry `session_id`, `cwd`, `transcript_path` and
`agent` from the payload, plus `launched_session` when `MUVUE_SESSION`
is set. A Claude Code hook in a linked worktree outside the repository
finds the repository through the worktree's `.git` file and only
records; so does a hook from the user-level install. Any error in a
Claude Code hook is spooled as `hook_error` and the hook exits 0.
```

- [ ] **Step 7: Let `muvue hook` run the new events**

In `src/muvue/cli/main.py:589`, change `if name not in _hook_mod.DECISION_HOOKS:` to:

```python
    if name not in _hook_mod.SESSION_HOOKS:
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_hook_sessions.py tests/test_hook_fast_path.py tests/test_claude_hook_decisions.py tests/test_hook_import_graph.py tests/test_adapters_cli.py tests/test_trailers_hooks.py -q`
Expected: PASS.

- [ ] **Step 9: Run the latency benchmark and the whole suite**

Run: `uv run pytest tests/test_hook_latency_benchmark.py -q && uv run pytest -q`
Expected: PASS. The benchmark's p95/p99 bars (60 ms / 120 ms) still hold.

- [ ] **Step 10: Commit**

```bash
git add src/muvue/_hook.py src/muvue/cli/main.py tests/test_hook_sessions.py
git commit -F - <<'MSG'
Spool Claude Code session lines from every hook, including worktrees

Hook lines now carry session_id, cwd, transcript_path and agent;
PreToolUse appends one line per call in a session; UserPromptSubmit and
SessionEnd are recorded; a linked worktree outside the repository finds
its repository through the .git file and only records; user-level hooks
only record; a crashing hook spools hook_error and exits 0.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 3: Claude Code adapter: new events, `--user` install, doctor report

**Files:**
- Modify: `src/muvue/core/adapters.py:65-118` (events, install), add helpers after `:129`
- Modify: `src/muvue/cli/main.py:487-509` (`adapter install --user`)
- Modify: `src/muvue/core/doctor.py:529-546` (report the active install)
- Create: `tests/test_adapter_user_scope.py`

**Interfaces:**
- Consumes: `muvue._hook.HOOK_SCOPE_ENV` semantics from Task 2 (commands prefixed `MUVUE_HOOK_SCOPE=user ` only record).
- Produces, in `muvue.core.adapters`:
  - `CLAUDE_HOOK_EVENTS: dict[str, str]` (six events), `USER_SCOPE_PREFIX = "MUVUE_HOOK_SCOPE=user "`
  - `install_claude_code(repo_root: Path, *, protocol_version: int) -> Path` (unchanged signature, now six events)
  - `user_settings_path(home: Path | None = None) -> Path`
  - `install_claude_code_user(*, protocol_version: int, home: Path | None = None) -> Path`
  - `claude_code_installs(repo_root: Path, home: Path | None = None) -> dict[str, Path]` (keys `"repo"`, `"user"`, in that order, only those installed)
  - `missing_claude_events(path: Path) -> list[str]`
- Doctor info line: `claude code hooks: repo, user` (or `not installed`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adapter_user_scope.py`:

```python
"""Mission control phase 1: the Claude Code adapter installs six hooks,
`--user` installs them machine-wide in ~/.claude/settings.json as
record-only hooks, `doctor` says which install is active, and a user
install survives `init`/`uninit` of a repository."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import adapters, doctor, repo_init
from muvue.core.config import PROTOCOL_VERSION

SIX = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PreCompact", "Stop", "SessionEnd"]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("MUVUE_SESSION", "MUVUE_HOOK_SCOPE", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


def _commands(settings: dict, event: str) -> list[str]:
    return [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]


def test_the_repo_install_writes_six_hooks(tmp_path, home):
    path = adapters.install_claude_code(tmp_path, protocol_version=PROTOCOL_VERSION)
    settings = json.loads(path.read_text())
    assert list(settings["hooks"]) == SIX
    for event in SIX:
        assert any("-S -m muvue._hook" in c and "MUVUE_HOOK_SCOPE" not in c for c in _commands(settings, event))


def test_the_user_install_prefixes_every_command_and_keeps_other_settings(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    path = adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    assert path == home / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    assert settings["theme"] == "dark"
    for event in SIX:
        assert all(c.startswith(adapters.USER_SCOPE_PREFIX) for c in _commands(settings, event))
    adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    again = json.loads(path.read_text())
    assert all(len(again["hooks"][event]) == 1 for event in SIX)  # idempotent


def test_doctor_reports_which_install_is_active(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_init.init_repo(repo)
    info = lambda: [i for i in doctor.run_doctor(repo, skip_security_probes=True).info if i.startswith("claude code hooks")]  # noqa: E731
    assert info() == ["claude code hooks: not installed"]
    adapters.install_claude_code(repo, protocol_version=PROTOCOL_VERSION)
    assert info() == ["claude code hooks: repo"]
    adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    assert info() == ["claude code hooks: repo, user"]


def test_doctor_warns_when_the_repo_install_lacks_the_session_events(tmp_path, home):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_init.init_repo(repo)
    path = adapters.install_claude_code(repo, protocol_version=PROTOCOL_VERSION)
    settings = json.loads(path.read_text())
    del settings["hooks"]["SessionEnd"]
    path.write_text(json.dumps(settings))
    report = doctor.run_doctor(repo, skip_security_probes=True)
    assert any("SessionEnd" in w and "adapter install claude-code" in w for w in report.warnings)
    assert report.ok


def test_a_user_install_survives_an_init_uninit_round_trip(tmp_path, home, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init_with_commit(repo)
    status = lambda: subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout  # noqa: E731
    before = status()

    repo_init.init_repo(repo)
    path = adapters.install_claude_code_user(protocol_version=PROTOCOL_VERSION)
    repo_init.uninit_repo(repo)

    assert status() == before
    assert not (repo / ".muvue").exists()
    assert list(json.loads(path.read_text())["hooks"]) == SIX  # machine-wide: uninit leaves it
    # The left-behind user hook, in a repository that is no longer
    # registered, exits 0 and writes nothing.
    monkeypatch.setenv("MUVUE_HOOK_SCOPE", "user")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": str(repo)})))
    assert _hook.main(["session-start", str(repo)]) == 0
    assert not (repo / ".muvue").exists()


def test_the_cli_installs_for_the_user(tmp_path, home):
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "adapter", "install", "claude-code", "--user"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(json.loads((home / ".claude" / "settings.json").read_text())["hooks"]) == SIX


def test_the_cli_refuses_user_for_other_adapters(tmp_path, home):
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        [sys.executable, "-m", "muvue", "adapter", "install", "codex", "--user"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "--user applies to claude-code only" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_adapter_user_scope.py -q`
Expected: FAIL. `AttributeError: module 'muvue.core.adapters' has no attribute 'install_claude_code_user'`; the six-hook test fails with four events; the CLI test exits 2 (`No such option: --user`).

- [ ] **Step 3: Rewrite the Claude Code installer in `src/muvue/core/adapters.py`**

Replace `:65-118` (from `_CLAUDE_HOOK_EVENTS = {` through the end of `install_claude_code`) with:

```python
CLAUDE_HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "user-prompt-submit",
    "PreToolUse": "pre-tool-use",
    "PreCompact": "pre-compact",
    "Stop": "stop",
    "SessionEnd": "session-end",
}
# Mission control phase 1: commands in ~/.claude/settings.json carry this
# prefix, which makes `muvue._hook` record the event without deciding
# anything (see `muvue._hook._session_hook`).
USER_SCOPE_PREFIX = "MUVUE_HOOK_SCOPE=user "


def _is_muvue_hook_command(command: str) -> bool:
    """Recognizes both the current fast-path command and the pre-v4
    full-CLI command, so re-running `adapter install claude-code`
    against a settings.json written before this session upgrades it in
    place instead of leaving a stale duplicate entry."""
    return "muvue._hook" in command or "-m muvue hook" in command


def _merge_claude_hooks(path: Path, *, protocol_version: int, prefix: str = "") -> Path:
    """Write/merge a Claude Code settings file's `hooks` section. Merges
    into an existing settings.json (never overwrites unrelated keys or
    other tools' hook entries -- plan section 5's shim principle, "append
    to existing hooks", applied to this JSON config instead of a shell
    script) and is idempotent: re-running replaces only muvue's own
    entries, detected by `_is_muvue_hook_command`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})

    for claude_event, muvue_name in CLAUDE_HOOK_EVENTS.items():
        # v4 section 4a (P0.5): the Claude Code adapter's hook commands
        # invoke the stdlib-only fast path too (`PreToolUse` fires per
        # tool call -- the whole reason this entry point exists), not
        # the full Typer CLI. Shared with the git-hook shim writer so
        # both stay in sync -- see core.repo_init.hook_fast_path_command.
        command = prefix + hook_fast_path_command(muvue_name)
        entry_list = [
            group
            for group in hooks.get(claude_event, [])
            if not any(_is_muvue_hook_command(h.get("command", "")) for h in group.get("hooks", []))
        ]
        group: dict = {"hooks": [{"type": "command", "command": command}]}
        if claude_event == "PreToolUse":
            group["matcher"] = "Edit|Write|Bash"
        entry_list.append(group)
        hooks[claude_event] = entry_list

    settings["_muvue"] = {"protocol_version": protocol_version}
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def install_claude_code(repo_root: Path, *, protocol_version: int) -> Path:
    """`.claude/settings.json` of one repository: the decision hooks
    (SessionStart, PreToolUse, PreCompact, Stop) plus the two record-only
    session events (UserPromptSubmit, SessionEnd)."""
    return _merge_claude_hooks(
        Path(repo_root) / ".claude" / "settings.json", protocol_version=protocol_version,
    )


def user_settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "settings.json"


def install_claude_code_user(*, protocol_version: int, home: Path | None = None) -> Path:
    """`muvue adapter install claude-code --user` (mission control phase
    1): the same six hooks in `~/.claude/settings.json`, so every Claude
    Code session on this machine reports, including sessions in worktrees
    whose checkout has no `.claude/settings.json`. These hooks only
    record: they never block an edit in a repository that did not install
    the adapter itself. `uninit` of one repository leaves them, since
    they serve every repository; in a directory that is not a registered
    repository they exit 0 at once."""
    return _merge_claude_hooks(
        user_settings_path(home), protocol_version=protocol_version, prefix=USER_SCOPE_PREFIX,
    )


def _muvue_hook_events(path: Path) -> set[str]:
    """The Claude Code events that run a muvue hook in one settings file."""
    if not path.exists():
        return set()
    try:
        settings = json.loads(path.read_text())
    except json.JSONDecodeError:
        return set()
    found = set()
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups if isinstance(groups, list) else []:
            hooks = group.get("hooks", []) if isinstance(group, dict) else []
            if any(isinstance(h, dict) and _is_muvue_hook_command(h.get("command", "")) for h in hooks):
                found.add(event)
    return found


def claude_code_installs(repo_root: Path, home: Path | None = None) -> dict[str, Path]:
    """Where muvue's Claude Code hooks are installed for this repository:
    `"repo"` (its `.claude/settings.json`), `"user"` (`~/.claude/
    settings.json`), both or neither."""
    candidates = {"repo": Path(repo_root) / ".claude" / "settings.json", "user": user_settings_path(home)}
    return {scope: path for scope, path in candidates.items() if _muvue_hook_events(path)}


def missing_claude_events(path: Path) -> list[str]:
    """Events a muvue install in `path` lacks, e.g. one written before
    UserPromptSubmit and SessionEnd were added."""
    installed = _muvue_hook_events(path)
    return [event for event in CLAUDE_HOOK_EVENTS if installed and event not in installed]
```

- [ ] **Step 4: Add `--user` to the CLI**

In `src/muvue/cli/main.py`, replace the `adapter_install` signature and the first two lines of its body (`:488-498`) so the function reads:

```python
@adapter_app.command("install", help="Connect an agent (claude-code, codex, gemini, cursor) to muvue.")
def adapter_install(
    name: str = typer.Argument(..., help="claude-code, codex, gemini, or cursor"),
    path: Path = typer.Option(Path("."), "--path"),
    user: bool = typer.Option(
        False, "--user",
        help="claude-code only: install record-only hooks in ~/.claude/settings.json, so every "
        "Claude Code session on this machine reports to muvue.",
    ),
) -> None:
    """Write the given vendor's config, pointing it at muvue's CLI/MCP
    surface (plan section 7). The Claude Code hook contract was checked
    against claude 2.1.281; the Codex/Gemini/Cursor writers are
    best-effort -- see docs/providers.md for what was verified."""
    if user:
        if name != "claude-code":
            typer.echo(f"--user applies to claude-code only, not {name!r}", err=True)
            raise typer.Exit(1)
        written = core.adapters.install_claude_code_user(protocol_version=core.config.PROTOCOL_VERSION)
        typer.echo(f"installed {name} adapter for every session of this user: {written}")
        return
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
```

(The rest of the function, from `installers = {`, is unchanged.)

- [ ] **Step 5: Report the install in `doctor`**

In `src/muvue/core/doctor.py`, insert immediately before `if repair and db_path.exists():` (`:548`), after the `if config is not None:` block that checks adapter protocol versions (this check does not need `config`):

```python
    # Mission control phase 1: which Claude Code install reports this
    # repository's sessions, and whether the repository's own install
    # predates the session events. A stale user-level install is only
    # listed: it is machine-wide, not this repository's to fix.
    claude_installs = adapters_mod.claude_code_installs(repo_root)
    report.info.append("claude code hooks: " + (", ".join(claude_installs) or "not installed"))
    if "repo" in claude_installs:
        missing = adapters_mod.missing_claude_events(claude_installs["repo"])
        if missing:
            report.warn(
                f"{claude_installs['repo']} has no muvue hook for {', '.join(missing)}, so agent "
                "sessions are not fully tracked; re-run `muvue adapter install claude-code`"
            )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_adapter_user_scope.py tests/test_adapters.py tests/test_adapters_cli.py tests/test_doctor_drivers.py tests/test_doctor_queue_depth.py tests/test_protocol_version.py tests/test_init_uninit.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/adapters.py src/muvue/cli/main.py src/muvue/core/doctor.py tests/test_adapter_user_scope.py
git commit -F - <<'MSG'
Install UserPromptSubmit and SessionEnd hooks, and add adapter install --user

The user-level install writes record-only hooks to ~/.claude/settings.json
so sessions in any worktree report; doctor says which install is active
and warns when the repository's own install lacks the session events.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 4: The drain folds session lines into `sessions` and `session_touches`

**Files:**
- Create: `src/muvue/core/sessions.py`
- Modify: `src/muvue/core/gitutil.py` (add `worktree_top`, `branch_at`)
- Modify: `src/muvue/core/hooks.py:40-44` (import), `:230-271` (`_process_queue_event`)
- Modify: `src/muvue/core/__init__.py` (export `sessions`)
- Create: `tests/test_session_drain.py`

**Interfaces:**
- Consumes: queue line shape from Task 2; tables from Task 1.
- Produces:
  - `muvue.core.gitutil.worktree_top(start: str) -> str | None` and `branch_at(start: str) -> str | None` (no subprocess)
  - `muvue.core.sessions`: `TS_FORMAT`, `SESSION_ID_RE`, `LAUNCHED_SESSION_ENV`, `SESSION_EVENTS`, `UNAUDITED_EVENTS = ("pre-tool-use", "heartbeat")`, `parse_ts(ts: str) -> datetime | None`, `apply_line(conn, event: dict) -> str | None` (returns the canonical session id it updated, or None)
  - Rules: a line creates only `origin = 'hand'` rows and never sets `pid`; node binding order is node worktree containing `cwd`, then the launched row's node (via `launched_session`, which makes the agent's own id an alias), then `bind_node_id`, else NULL. `last_action` values: `session started`, `prompt sent`, `<Tool> <path>` or `<Tool>`, `turn ended`, `compacting`, `session ended`, or a heartbeat's `action`. `waiting` is 1 only after `stop`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_drain.py`:

```python
"""Mission control phase 1: `drain_queue` folds session lines into
`sessions` and `session_touches` in the same transaction as the
`hook.<event>` audit events."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import db as core_db, hooks, nodes, projects
from muvue.core.repo_init import init_repo

OLD = "2000-01-01T00:00:00.000Z"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _spool(repo: Path, **line) -> None:
    line.setdefault("ts", _hook._now_iso())
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _row(conn, session_id: str):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _line(repo: Path, event: str, session_id: str = "s-1", **extra) -> dict:
    return {"event": event, "node_id": None, "session_id": session_id, "cwd": str(repo), "agent": "claude", **extra}


def test_a_session_start_line_creates_a_hand_session(repo, conn):
    _spool(repo, **_line(repo, "session-start", transcript_path="/t/s-1.jsonl"))
    _drain(conn, repo)
    row = _row(conn, "s-1")
    assert (row["origin"], row["agent"], row["cwd"], row["branch"]) == ("hand", "claude", str(repo), "main")
    assert (row["node_id"], row["project_id"], row["pid"]) == (None, None, None)
    assert row["transcript_path"] == "/t/s-1.jsonl"
    assert row["last_action"] == "session started"
    types = [r["type"] for r in conn.execute("SELECT type FROM events WHERE type LIKE 'hook.%'")]
    assert types == ["hook.session-start"]


def test_pre_tool_use_records_the_touched_path_and_no_audit_event(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "pre-tool-use", tool_name="Edit", file_path=str(repo / "src" / "app.py")))
    _drain(conn, repo)
    assert _row(conn, "s-1")["last_action"] == "Edit src/app.py"
    touches = [r["path"] for r in conn.execute("SELECT path FROM session_touches WHERE session_id = 's-1'")]
    assert touches == ["src/app.py"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'hook.pre-tool-use'").fetchone()[0] == 0


def test_stop_sets_waiting_and_a_prompt_clears_it(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "stop"))
    _drain(conn, repo)
    assert (_row(conn, "s-1")["waiting"], _row(conn, "s-1")["last_action"]) == (1, "turn ended")
    _spool(repo, **_line(repo, "user-prompt-submit"))
    _drain(conn, repo)
    assert (_row(conn, "s-1")["waiting"], _row(conn, "s-1")["last_action"]) == (0, "prompt sent")


def test_session_end_sets_ended_at_and_a_resume_clears_it(repo, conn):
    _spool(repo, **_line(repo, "session-start"))
    _spool(repo, **_line(repo, "session-end", reason="logout"))
    _drain(conn, repo)
    assert _row(conn, "s-1")["ended_at"] is not None
    _spool(repo, **_line(repo, "session-start"))
    _drain(conn, repo)
    assert _row(conn, "s-1")["ended_at"] is None


def test_a_session_in_a_node_worktree_is_bound_to_that_node(repo, conn, tmp_path):
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "node-x", str(wt)], cwd=repo, check=True, capture_output=True)
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
    (wt / "sub").mkdir()
    _spool(repo, **{**_line(repo, "session-start", "s-2"), "cwd": str(wt / "sub")})
    _drain(conn, repo)
    row = _row(conn, "s-2")
    assert (row["node_id"], row["project_id"], row["branch"]) == (node["id"], project["id"], "node-x")


def test_the_current_node_file_never_binds_a_session(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    (repo / ".muvue" / "current_node").write_text(str(node["id"]))
    _spool(repo, **{**_line(repo, "session-start", "s-3"), "node_id": node["id"]})
    _drain(conn, repo)
    assert _row(conn, "s-3")["node_id"] is None


def test_lines_from_a_launched_agent_fold_into_the_launched_row(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-1', ?, ?, 'claude', 'launched', ?, ?, ?)",
        (project["id"], node["id"], str(repo), OLD, OLD),
    )
    _spool(repo, **_line(repo, "pre-tool-use", "claude-own", tool_name="Write", file_path=str(repo / "a.py"),
                         launched_session="run-1"))
    _drain(conn, repo)
    assert _row(conn, "claude-own")["alias_of"] == "run-1"
    run = _row(conn, "run-1")
    assert run["last_action"] == "Write a.py"
    assert run["last_event_at"] > OLD
    assert [tuple(r) for r in conn.execute("SELECT session_id, path FROM session_touches")] == [("run-1", "a.py")]
    # A later line with no launched_session still lands on the run.
    _spool(repo, **_line(repo, "stop", "claude-own"))
    _drain(conn, repo)
    assert _row(conn, "run-1")["waiting"] == 1


def test_an_unknown_launched_session_leaves_a_plain_hand_session(repo, conn):
    _spool(repo, **_line(repo, "session-start", "s-4", launched_session="run-missing"))
    _drain(conn, repo)
    row = _row(conn, "s-4")
    assert (row["origin"], row["alias_of"]) == ("hand", None)


def test_a_queue_line_cannot_set_pid_or_origin(repo, conn):
    _spool(repo, **_line(repo, "session-start", "s-5", origin="launched", pid=1))
    _drain(conn, repo)
    row = _row(conn, "s-5")
    assert (row["origin"], row["pid"]) == ("hand", None)


def test_a_path_outside_any_git_worktree_is_not_recorded(repo, conn, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _spool(repo, **_line(repo, "pre-tool-use", tool_name="Edit", file_path=str(outside / "x.py")))
    _drain(conn, repo)
    assert _row(conn, "s-1")["last_action"] == "Edit"
    assert conn.execute("SELECT COUNT(*) FROM session_touches").fetchone()[0] == 0


@pytest.mark.parametrize("line", [
    {"event": "session-start", "session_id": "has space", "cwd": "/x", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "relative/dir", "agent": "claude"},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": 7},
    {"event": "session-start", "session_id": "s-6", "cwd": "/x", "agent": "claude", "ts": "yesterday"},
    {"event": "post-merge", "session_id": "s-6", "cwd": "/x", "agent": "claude"},
])
def test_malformed_session_lines_make_no_session(repo, conn, line):
    _spool(repo, **line)
    _drain(conn, repo)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session_drain.py -q`
Expected: FAIL. Every test that reads a row fails with `TypeError: 'NoneType' object is not subscriptable` (no session row is written yet); the malformed-line cases pass already.

- [ ] **Step 3: Add the stdlib git readers to `src/muvue/core/gitutil.py`**

Add `import os` to the imports (above `import subprocess`), and append:

```python
def worktree_top(start: str) -> str | None:
    """The nearest directory at or above `start` holding a `.git` entry (a
    directory for a main checkout, a file for a linked worktree). No
    subprocess: the queue drain calls this for every touched path."""
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def branch_at(start: str) -> str | None:
    """The branch checked out in the worktree containing `start`, read
    from its HEAD file. None when detached, or not inside a git worktree."""
    top = worktree_top(start)
    if top is None:
        return None
    git_dir = os.path.join(top, ".git")
    if os.path.isfile(git_dir):
        try:
            with open(git_dir) as f:
                first = f.readline().strip()
        except OSError:
            return None
        if not first.startswith("gitdir:"):
            return None
        git_dir = first[len("gitdir:"):].strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(top, git_dir)
    try:
        with open(os.path.join(git_dir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None
```

- [ ] **Step 4: Create `src/muvue/core/sessions.py`**

```python
"""Agent sessions (mission control phase 1, docs/superpowers/specs/
2026-10-10-mission-control-design.md): one row per run of an agent CLI,
started by muvue's runner (`launched`) or by a person (`hand`).

Rows have two writers. The runner inserts and ends its own `launched`
rows. Everything else arrives as `.muvue/queue.jsonl` lines -- the
Claude Code hooks (`muvue._hook`) and the MCP session tools -- and is
folded in by `apply_line`, inside the drain's transaction. The queue is
untrusted input: a line can create or update only a `hand` row, never
sets `pid` (the one field `POST /sessions/{id}/stop` signals), and is
validated field by field.

A session's node is decided here, not in the hook: a `cwd` inside a
node's worktree, else the runner's node for a launched agent (the
agent's own session id becomes an alias of the launched row, so one
run is one session), else an explicit `bind_node_id` from the MCP
`session_start` tool, else none (off-plan). `.muvue/current_node` is
never used: it is one file per repository.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone

from muvue import _hook

from . import db as db_mod
from . import gitutil

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LAUNCHED_SESSION_ENV = _hook.LAUNCHED_SESSION_ENV
SESSION_EVENTS = (
    "session-start", "user-prompt-submit", "pre-tool-use", "stop", "pre-compact", "session-end", "heartbeat",
)
# Session lines that are not also recorded as `hook.<event>` audit
# events: PreToolUse fires once per tool call and an MCP heartbeat as
# often as an agent likes, so auditing them would flood `events`.
UNAUDITED_EVENTS = ("pre-tool-use", "heartbeat")
_ACTIONS = {
    "session-start": "session started",
    "user-prompt-submit": "prompt sent",
    "stop": "turn ended",
    "pre-compact": "compacting",
    "session-end": "session ended",
}
_MAX_TOUCHED_PER_LINE = 100


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _valid_id(value) -> bool:
    return isinstance(value, str) and SESSION_ID_RE.match(value) is not None


def _canonical_id(conn: sqlite3.Connection, session_id: str, launched) -> str:
    """The row a line updates. A launched agent's lines name the runner's
    session (`launched_session`, from `MUVUE_SESSION`); the agent's own
    id is recorded once as an alias of it."""
    if _valid_id(launched) and launched != session_id:
        found = db_mod.query_one(
            conn, "SELECT id FROM sessions WHERE id = ? AND origin = 'launched' AND alias_of IS NULL", (launched,),
        )
        if found is not None:
            with db_mod.write_txn(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, project_id, node_id, agent, origin, cwd, "
                    "started_at, last_event_at, alias_of) SELECT ?, project_id, node_id, agent, "
                    "'launched', cwd, started_at, last_event_at, id FROM sessions WHERE id = ?",
                    (session_id, launched),
                )
            return launched
    row = db_mod.query_one(conn, "SELECT alias_of FROM sessions WHERE id = ?", (session_id,))
    if row is not None and row["alias_of"] is not None:
        return row["alias_of"]
    return session_id


def _node_for_cwd(conn: sqlite3.Connection, cwd: str) -> sqlite3.Row | None:
    """The live node whose worktree contains `cwd` (deepest wins)."""
    best = None
    for row in db_mod.query_all(
        conn, "SELECT id, project_id, worktree FROM nodes WHERE worktree IS NOT NULL AND deleted_at IS NULL",
    ):
        top = os.path.realpath(row["worktree"])
        if (cwd == top or cwd.startswith(top + os.sep)) and (best is None or len(top) > len(best[0])):
            best = (top, row)
    return best[1] if best else None


def _explicit_node(conn: sqlite3.Connection, node_id) -> sqlite3.Row | None:
    if type(node_id) is not int:
        return None
    return db_mod.query_one(
        conn, "SELECT id, project_id FROM nodes WHERE id = ? AND deleted_at IS NULL", (node_id,),
    )


def _insert_hand(conn: sqlite3.Connection, session_id: str, *, agent: str, cwd: str, ts: str, event: dict) -> None:
    node = _node_for_cwd(conn, cwd) or _explicit_node(conn, event.get("bind_node_id"))
    transcript = event.get("transcript_path")
    with db_mod.write_txn(conn):
        conn.execute(
            "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, branch, transcript_path, "
            "started_at, last_event_at) VALUES (?, ?, ?, ?, 'hand', ?, ?, ?, ?, ?)",
            (
                session_id, node["project_id"] if node else None, node["id"] if node else None, agent[:64],
                cwd, gitutil.branch_at(cwd), transcript if isinstance(transcript, str) else None, ts, ts,
            ),
        )


def _move(conn: sqlite3.Connection, row: sqlite3.Row, cwd: str) -> None:
    """The session's directory changed: a hand session follows it into a
    node's worktree; a launched session keeps the runner's node."""
    node_id, project_id = row["node_id"], row["project_id"]
    if row["origin"] == "hand":
        node = _node_for_cwd(conn, cwd)
        if node is not None:
            node_id, project_id = node["id"], node["project_id"]
    with db_mod.write_txn(conn):
        conn.execute(
            "UPDATE sessions SET cwd = ?, branch = ?, node_id = ?, project_id = ? WHERE id = ?",
            (cwd, gitutil.branch_at(cwd), node_id, project_id, row["id"]),
        )


def _raw_paths(event: dict) -> list[str]:
    if event.get("event") == "pre-tool-use":
        path = event.get("file_path")
        return [path] if isinstance(path, str) and path else []
    if event.get("event") == "heartbeat":
        paths = event.get("touched_paths")
        if isinstance(paths, list):
            return [p for p in paths[:_MAX_TOUCHED_PER_LINE] if isinstance(p, str) and p]
    return []


def _repo_relative(path: str, cwd: str) -> str | None:
    """`path` relative to the git worktree that contains it; None outside
    any worktree."""
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    top = gitutil.worktree_top(os.path.dirname(full))
    if top is None:
        return None
    rel = os.path.relpath(full, top)
    return None if rel == "." or rel.startswith("..") else rel


def _action(event: dict, paths: list[str]) -> str | None:
    etype = event.get("event")
    if etype == "pre-tool-use":
        tool = event.get("tool_name")
        tool = tool if isinstance(tool, str) and tool else "tool"
        return f"{tool} {paths[0]}" if paths else tool
    if etype == "heartbeat":
        action = event.get("action")
        return action.strip()[:200] if isinstance(action, str) and action.strip() else None
    return _ACTIONS.get(etype)


def apply_line(conn: sqlite3.Connection, event: dict) -> str | None:
    """Fold one queue line into `sessions`/`session_touches`. Returns the
    session id it updated, or None when the line names no valid session
    (most queue lines: commits, pushes, timeouts). Runs in its own
    `write_txn`, which nests into the drain's when there is one."""
    session_id, ts = event.get("session_id"), event.get("ts")
    if event.get("event") not in SESSION_EVENTS or not _valid_id(session_id):
        return None
    if not isinstance(ts, str) or parse_ts(ts) is None:
        return None
    raw_cwd = event.get("cwd")
    cwd = os.path.realpath(raw_cwd) if isinstance(raw_cwd, str) and os.path.isabs(raw_cwd) else None
    with db_mod.write_txn(conn):
        canonical = _canonical_id(conn, session_id, event.get("launched_session"))
        row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (canonical,))
        if row is None:
            agent = event.get("agent")
            if cwd is None or not isinstance(agent, str) or not agent.strip():
                return None
            _insert_hand(conn, canonical, agent=agent.strip(), cwd=cwd, ts=ts, event=event)
            row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (canonical,))
        elif cwd is not None and cwd != row["cwd"]:
            _move(conn, row, cwd)
        paths = [p for p in (_repo_relative(raw, cwd or row["cwd"]) for raw in _raw_paths(event)) if p]
        etype = event["event"]
        conn.execute(
            "UPDATE sessions SET last_event_at = MAX(last_event_at, ?), "
            "last_action = COALESCE(?, last_action), waiting = ?, "
            "ended_at = CASE WHEN ? = 'session-end' THEN ? WHEN ? = 'session-start' THEN NULL "
            "ELSE ended_at END WHERE id = ?",
            (ts, _action(event, paths), int(etype == "stop"), etype, ts, etype, canonical),
        )
        for path in paths:
            conn.execute(
                "INSERT INTO session_touches (session_id, path, at) VALUES (?, ?, ?) "
                "ON CONFLICT (session_id, path) DO UPDATE SET at = excluded.at",
                (canonical, path, ts),
            )
    return canonical
```

- [ ] **Step 5: Call it from the drain**

In `src/muvue/core/hooks.py`, add to the imports (after `from . import nodes as nodes_mod`):

```python
from . import sessions as sessions_mod
```

In `_process_queue_event`, after the `post-commit` block (`:241-245`), insert:

```python
    if etype in sessions_mod.UNAUDITED_EVENTS:
        # Mission control phase 1: one line per tool call or heartbeat
        # updates the session only; see `core.sessions.UNAUDITED_EVENTS`.
        sessions_mod.apply_line(conn, event)
        return
```

and replace the closing `with db_mod.write_txn(conn):` block (`:262-271`) with:

```python
    with db_mod.write_txn(conn):
        events_mod.record_event(
            conn,
            project_id=project_id,
            node_id=node_id,
            actor="hook",
            actor_evidence="hook",
            type_=f"hook.{etype}",
            payload=event,
        )
        # Same transaction as the audit event: a session line and its
        # `hook.*` event land together or not at all.
        sessions_mod.apply_line(conn, event)
```

In `src/muvue/core/__init__.py`, add `sessions,` between `runners,` and `spend,` in the `from . import (...)` list, and `"sessions",` between `"runners",` and `"spend",` in `__all__`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session_drain.py tests/test_queue_drain.py tests/test_txn_discipline.py tests/test_trailers_hooks.py -q`
Expected: PASS. `test_txn_discipline` confirms every `execute` in `sessions.py` sits inside a `write_txn`/`read_txn` block.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/sessions.py src/muvue/core/gitutil.py src/muvue/core/hooks.py src/muvue/core/__init__.py tests/test_session_drain.py
git commit -F - <<'MSG'
Fold hook session lines into sessions and session_touches at drain time

Nodes are bound from the session's worktree, then the launched run
(whose agent id becomes an alias), then an explicit MCP node; never from
.muvue/current_node. Queue lines can only create hand sessions and never
set a pid. PreToolUse lines update the session without an audit event.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 5: Liveness and the session list

**Files:**
- Modify: `src/muvue/core/sessions.py` (imports; append `session_state`, `list_sessions`)
- Create: `tests/test_session_state.py`

**Interfaces:**
- Consumes: `SessionsConfig` (Task 1); `apply_line`, `parse_ts` (Task 4).
- Produces, in `muvue.core.sessions`:
  - `STATES = ("working", "waiting", "quiet", "idle", "ended")`
  - `session_state(row, now: datetime, cfg: SessionsConfig) -> str` (`row` is a `sqlite3.Row` or a dict with `last_event_at`, `waiting`, `ended_at`)
  - `list_sessions(conn, cfg: SessionsConfig, *, project_id: int | None = None, include_ended: bool = False, now: datetime | None = None) -> list[dict]`, newest event first, alias rows excluded. Each dict: every `sessions` column, plus `waiting: bool`, `state: str`, `node_title`, `node_status`, `worktree` (the node's worktree, else the git worktree containing `cwd`), `touches: list[str]` (newest first), `spend: {"in_tokens", "out_tokens", "cost"} | None` (launched sessions with a node only).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_state.py`:

```python
"""Mission control phase 1: liveness is computed on read from
last_event_at, waiting and ended_at, with the [sessions] thresholds
(120 s working, 900 s idle, 7200 s ended; "quiet" in between working
and idle)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue.core import db as core_db, hooks, nodes, projects, sessions
from muvue.core.config import SessionsConfig
from muvue.core.repo_init import init_repo

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
CFG = SessionsConfig()


def _ts(age_s: float) -> str:
    t = NOW - timedelta(seconds=age_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _state_row(age_s: float, *, waiting: int = 0, ended_at: str | None = None) -> dict:
    return {"last_event_at": _ts(age_s), "waiting": waiting, "ended_at": ended_at}


@pytest.mark.parametrize("row, state", [
    (_state_row(5), "working"),
    (_state_row(120), "working"),
    (_state_row(121), "quiet"),
    (_state_row(900), "quiet"),
    (_state_row(901), "idle"),
    (_state_row(7200), "idle"),
    (_state_row(7201), "ended"),
    (_state_row(500, waiting=1), "waiting"),
    (_state_row(7201, waiting=1), "ended"),
    (_state_row(5, ended_at=_ts(1)), "ended"),
])
def test_session_state(row, state):
    assert sessions.session_state(row, NOW, CFG) == state


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _spool(repo: Path, event: str, session_id: str, age_s: float, **extra) -> None:
    line = {"event": event, "ts": _ts(age_s), "node_id": None, "session_id": session_id,
            "cwd": str(repo), "agent": "claude", **extra}
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _states(conn, **kw) -> dict[str, str]:
    return {s["id"]: s["state"] for s in sessions.list_sessions(conn, CFG, now=NOW, **kw)}


def test_drained_lines_move_a_session_through_its_states(repo, conn):
    _spool(repo, "session-start", "s-1", 60)
    _spool(repo, "pre-tool-use", "s-1", 30, tool_name="Bash")
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "working"}
    _spool(repo, "stop", "s-1", 20)
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "waiting"}
    _spool(repo, "user-prompt-submit", "s-1", 10)
    _drain(conn, repo)
    assert _states(conn) == {"s-1": "working"}
    _spool(repo, "session-end", "s-1", 5)
    _drain(conn, repo)
    assert _states(conn) == {}
    assert _states(conn, include_ended=True) == {"s-1": "ended"}


def test_quiet_and_idle_come_from_the_clock_alone(repo, conn):
    _spool(repo, "session-start", "quiet-one", 300)
    _spool(repo, "session-start", "idle-one", 1000)
    _drain(conn, repo)
    assert _states(conn) == {"quiet-one": "quiet", "idle-one": "idle"}


def test_the_list_is_newest_first_and_carries_touches(repo, conn):
    _spool(repo, "session-start", "older", 50)
    _spool(repo, "session-start", "newer", 40)
    _spool(repo, "pre-tool-use", "newer", 30, tool_name="Edit", file_path=str(repo / "a.py"))
    _spool(repo, "pre-tool-use", "newer", 20, tool_name="Write", file_path=str(repo / "b.py"))
    _drain(conn, repo)
    listed = sessions.list_sessions(conn, CFG, now=NOW)
    assert [s["id"] for s in listed] == ["newer", "older"]
    newer = listed[0]
    assert newer["touches"] == ["b.py", "a.py"]
    assert newer["waiting"] is False
    assert newer["worktree"] == str(repo)
    assert newer["spend"] is None


def test_project_filter_keeps_off_plan_sessions(repo, conn, tmp_path):
    mine = projects.create_project(conn, goal="mine")
    other = projects.create_project(conn, goal="other")
    node = nodes.create_node(conn, project_id=other["id"], kind="task", title="t", status="ready")
    wt = tmp_path / "wt"
    wt.mkdir()
    conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
    _spool(repo, "session-start", "off-plan", 10)
    _spool(repo, "session-start", "theirs", 10, cwd=str(wt))
    _drain(conn, repo)
    assert set(_states(conn, project_id=mine["id"])) == {"off-plan"}
    assert set(_states(conn, project_id=other["id"])) == {"off-plan", "theirs"}


def test_a_launched_session_shows_its_nodes_spend_and_hides_its_alias(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="Detect pitch", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-1', ?, ?, 'claude', 'launched', ?, ?, ?)",
        (project["id"], node["id"], str(repo), _ts(30), _ts(30)),
    )
    conn.execute(
        "INSERT INTO node_usage (node_id, agent, in_tokens, out_tokens, requests, cost) VALUES (?, 'claude', 1000, 200, 1, 0.12)",
        (node["id"],),
    )
    _spool(repo, "pre-tool-use", "claude-own", 10, tool_name="Bash", launched_session="run-1")
    _drain(conn, repo)
    listed = sessions.list_sessions(conn, CFG, now=NOW)
    assert [s["id"] for s in listed] == ["run-1"]
    run = listed[0]
    assert (run["node_title"], run["node_status"], run["state"]) == ("Detect pitch", "ready", "working")
    assert run["spend"] == {"in_tokens": 1000, "out_tokens": 200, "cost": pytest.approx(0.12)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session_state.py -q`
Expected: FAIL with `AttributeError: module 'muvue.core.sessions' has no attribute 'session_state'` (and `list_sessions`).

- [ ] **Step 3: Implement**

In `src/muvue/core/sessions.py`, add to the imports:

```python
from .config import SessionsConfig
```

Append:

```python
STATES = ("working", "waiting", "quiet", "idle", "ended")


def session_state(row, now: datetime, cfg: SessionsConfig) -> str:
    """Liveness, computed on read. Ended (ended_at set, or silent past
    `ended_s`) wins, then waiting on you (`waiting` after Stop), then
    working (an event within `working_s`), then idle (silent past
    `idle_s`). Between `working_s` and `idle_s` a session that is not
    waiting is "quiet": it may be in a long tool call, or gone."""
    if row["ended_at"] is not None:
        return "ended"
    last = parse_ts(row["last_event_at"])
    age = (now - last).total_seconds() if last is not None else float("inf")
    if age > cfg.ended_s:
        return "ended"
    if row["waiting"]:
        return "waiting"
    if age <= cfg.working_s:
        return "working"
    if age > cfg.idle_s:
        return "idle"
    return "quiet"


def _launched_spend(conn: sqlite3.Connection, node_id: int, agent: str) -> dict:
    """`node_usage` has no session column, so a launched session shows
    what its agent has used on its node, across every run of that node."""
    row = db_mod.query_one(
        conn,
        "SELECT COALESCE(SUM(in_tokens), 0) AS i, COALESCE(SUM(out_tokens), 0) AS o, "
        "COALESCE(SUM(cost), 0) AS c FROM node_usage WHERE node_id = ? AND agent = ?",
        (node_id, agent),
    )
    return {"in_tokens": row["i"], "out_tokens": row["o"], "cost": row["c"]}


def _view(conn: sqlite3.Connection, row: sqlite3.Row, state: str) -> dict:
    view = {key: row[key] for key in row.keys() if key != "node_worktree"}
    view["waiting"] = bool(row["waiting"])
    view["state"] = state
    view["worktree"] = row["node_worktree"] or gitutil.worktree_top(row["cwd"])
    view["touches"] = [
        r["path"] for r in db_mod.query_all(
            conn, "SELECT path FROM session_touches WHERE session_id = ? ORDER BY at DESC, path", (row["id"],),
        )
    ]
    view["spend"] = (
        _launched_spend(conn, row["node_id"], row["agent"])
        if row["origin"] == "launched" and row["node_id"] is not None else None
    )
    return view


def list_sessions(
    conn: sqlite3.Connection,
    cfg: SessionsConfig,
    *,
    project_id: int | None = None,
    include_ended: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Every session, newest event first, with its computed state. Alias
    rows (a launched agent's own id) are folded into their run. With
    `project_id`, off-plan sessions (no project) are kept: they belong to
    the repository, not to one project."""
    now = now or datetime.now(timezone.utc)
    sql = (
        "SELECT s.*, n.title AS node_title, n.status AS node_status, n.worktree AS node_worktree "
        "FROM sessions s LEFT JOIN nodes n ON n.id = s.node_id WHERE s.alias_of IS NULL"
    )
    params: list = []
    if project_id is not None:
        sql += " AND (s.project_id = ? OR s.project_id IS NULL)"
        params.append(project_id)
    out = []
    for row in db_mod.query_all(conn, sql + " ORDER BY s.last_event_at DESC, s.id", params):
        state = session_state(row, now, cfg)
        if state == "ended" and not include_ended:
            continue
        out.append(_view(conn, row, state))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session_state.py tests/test_session_drain.py tests/test_txn_discipline.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/muvue/core/sessions.py tests/test_session_state.py
git commit -F - <<'MSG'
Compute session liveness on read and list sessions with touches and spend

Working within 120 s, waiting after Stop, quiet until 900 s, idle until
7200 s, then ended; thresholds come from [sessions].

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 6: The runner records launched sessions

**Files:**
- Modify: `src/muvue/core/drivers.py:300-309` (`_run_streaming`), `:357-399` (`invoke_driver`)
- Modify: `src/muvue/core/runner.py:20-46` (imports), `:476-483` (`run_node`)
- Modify: `src/muvue/core/sessions.py` (append `format_ts`, `start_launched`, `set_pid`, `end_launched`)
- Modify: `tests/test_runner_live_findings.py:52` (the fake accepts the new keyword arguments)
- Create: `tests/test_launched_sessions.py`

**Interfaces:**
- Consumes: `apply_line` alias rule (Task 4), `list_sessions` (Task 5).
- Produces:
  - `muvue.core.drivers.invoke_driver(agent_name, agent_cfg, brief_text, cwd, *, timeout=..., log_path=None, env: dict[str, str] | None = None, on_spawn: Callable[[int], None] | None = None) -> DriverResult`. `env` replaces the child's environment; `on_spawn(pid)` runs right after the process starts (pid = its process group id); if `on_spawn` raises, the process group is killed and the error propagates.
  - `muvue.core.sessions.format_ts(dt: datetime) -> str` (millisecond, same shape the hook writes)
  - `muvue.core.sessions.start_launched(conn, *, node_id: int, project_id: int, agent: str, cwd: str) -> str` (returns `run-<uuid>`)
  - `muvue.core.sessions.set_pid(conn, session_id: str, pid: int) -> None`
  - `muvue.core.sessions.end_launched(conn, session_id: str) -> None` (sets `ended_at`, `last_action = 'run ended'`; no-op if already ended)
  - The runner passes `MUVUE_SESSION=<run id>` in the driver's environment, and ends the session after draining the agent's spooled lines.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_launched_sessions.py`:

```python
"""Mission control phase 1: a runner invocation is a `launched` session
from before the driver starts until it returns; the driver gets
MUVUE_SESSION, and the agent's own hook lines fold into that row."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from muvue import _hook
from muvue.core import db as core_db, drivers, gates, nodes, projects, runner as runner_mod, sessions
from muvue.core.config import AgentConfig, ChecksConfig, MuvueConfig, RoutingConfig, SessionsConfig
from muvue.core.drivers import DriverResult
from muvue.core.repo_init import init_repo

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def test_invoke_driver_passes_env_and_reports_the_pid(tmp_path):
    cfg = AgentConfig(command='printf %s "$MUVUE_SESSION"', cost_model="tokens")
    pids: list[int] = []
    result = drivers.invoke_driver(
        "probe", cfg, "", tmp_path, env={**os.environ, "MUVUE_SESSION": "run-abc"}, on_spawn=pids.append,
    )
    assert result.status == "done"
    assert result.raw_stdout == "run-abc"
    assert len(pids) == 1 and pids[0] > 0


def test_a_failing_on_spawn_kills_the_driver(tmp_path):
    cfg = AgentConfig(command="sleep 30", cost_model="tokens")
    seen: list[int] = []

    def boom(pid: int) -> None:
        seen.append(pid)
        raise RuntimeError("could not record the pid")

    with pytest.raises(RuntimeError, match="could not record the pid"):
        drivers.invoke_driver("probe", cfg, "", tmp_path, on_spawn=boom)
    with pytest.raises(ProcessLookupError):
        os.killpg(seen[0], 0)
    assert not drivers._ACTIVE


def test_run_node_records_a_launched_session_and_folds_the_agents_own_id(tmp_path, monkeypatch):
    monkeypatch.delenv("MUVUE_SESSION", raising=False)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True, env=GIT_ENV)
    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    config = MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"stub": AgentConfig(command="unused", cost_model="tokens")},
        routing=RoutingConfig(spec="stub", task="stub", subtask="stub"),
    )
    project = projects.create_project(conn, goal="g")
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t", criteria=["c"],
        criteria_mode="auto", predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    seen: dict = {}

    def agent(agent_name, agent_cfg, brief, cwd, *, log_path=None, env=None, on_spawn=None):
        run_id = env[sessions.LAUNCHED_SESSION_ENV]
        on_spawn(4242)
        seen["run_id"] = run_id
        seen["during"] = dict(conn.execute("SELECT origin, pid, ended_at, node_id FROM sessions WHERE id = ?", (run_id,)).fetchone())
        # What the agent's own Claude Code hooks would spool.
        monkeypatch.setenv("MUVUE_SESSION", run_id)
        _hook.run("pre-tool-use", str(tmp_path), {
            "session_id": "claude-own-1", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"},
        })
        return DriverResult(status="failed", error="stub agent gives up")

    runner_mod.run_node(conn, nodes.get_node(conn, task["id"]), "stub", config.agents["stub"], config, tmp_path, invoke=agent)

    run_id = seen["run_id"]
    assert run_id.startswith("run-")
    assert seen["during"] == {"origin": "launched", "pid": 4242, "ended_at": None, "node_id": task["id"]}
    after = conn.execute("SELECT * FROM sessions WHERE id = ?", (run_id,)).fetchone()
    assert after["ended_at"] is not None
    assert after["last_action"] == "run ended"
    assert conn.execute("SELECT alias_of FROM sessions WHERE id = 'claude-own-1'").fetchone()["alias_of"] == run_id
    listed = sessions.list_sessions(conn, SessionsConfig(), include_ended=True)
    assert [s["id"] for s in listed] == [run_id]
    conn.close()


def test_end_launched_does_not_overwrite_an_earlier_end(tmp_path):
    conn = core_db.init_db(tmp_path / "muvue.db")
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    run_id = sessions.start_launched(conn, node_id=node["id"], project_id=project["id"], agent="claude", cwd=str(tmp_path))
    sessions.end_launched(conn, run_id)
    first = conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"]
    sessions.end_launched(conn, run_id)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] == first
    conn.close()
```

In `tests/test_runner_live_findings.py:52`, change the fake's signature so it accepts the runner's new keyword arguments:

```python
    def committing_agent(agent_name, agent_cfg, brief, cwd, *, log_path=None, **kw):
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_launched_sessions.py -q`
Expected: FAIL. `TypeError: invoke_driver() got an unexpected keyword argument 'env'`, and `seen` stays empty in the runner test (`KeyError: 'run_id'`, since `run_node` never passes `env`).

- [ ] **Step 3: Let the driver take an environment and report its pid**

In `src/muvue/core/drivers.py`, replace `_run_streaming`'s signature and its first lines up to and including `_ACTIVE.add(proc)` (`:300-315`) with:

```python
def _run_streaming(
    command: str, cwd: Path, stdin_text: str, *, timeout: int, log_path: Path | None,
    env: dict[str, str] | None = None, on_spawn: Callable[[int], None] | None = None,
) -> tuple[int, str, str]:
    """Run `command` in its own session, feed `stdin_text`, and collect
    stdout/stderr -- copying each line to `log_path` as it arrives so a
    running node's output can be tailed (`GET /nodes/{id}/logs`). Raises
    `subprocess.TimeoutExpired` after killing the process group.

    `on_spawn(pid)` runs as soon as the process exists (the runner records
    it on the session row). If it raises, the process group is killed and
    the error propagates: a driver nobody can stop must not keep running."""
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True, env=env,
    )
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
    if on_spawn is not None:
        try:
            on_spawn(proc.pid)
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            with _ACTIVE_LOCK:
                _ACTIVE.discard(proc)
            raise
```

In `invoke_driver`, add the two keyword parameters after `log_path: Path | None = None,`:

```python
    env: dict[str, str] | None = None,
    on_spawn: Callable[[int], None] | None = None,
```

append to its docstring:

```
    `env`: the command's whole environment (None inherits this
    process's). `on_spawn(pid)`: called once the command has started;
    see `_run_streaming`.
```

and change the `_run_streaming(...)` call to:

```python
        returncode, stdout, stderr = _run_streaming(
            agent_cfg.command, cwd, brief_text, timeout=timeout, log_path=log_path,
            env=env, on_spawn=on_spawn,
        )
```

(`os`, `signal` (`drivers.py:28-29`) and `Callable` (`:35`) are already imported.)

- [ ] **Step 4: Add the launched-row helpers to `src/muvue/core/sessions.py`**

Add `import uuid` to the imports and append:

```python
def format_ts(dt: datetime) -> str:
    """Millisecond timestamps in the shape `muvue._hook._now_iso` writes,
    so stored values compare correctly as text."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _now_ts() -> str:
    return format_ts(datetime.now(timezone.utc))


def start_launched(conn: sqlite3.Connection, *, node_id: int, project_id: int, agent: str, cwd: str) -> str:
    """Insert the `launched` row for one runner invocation, before the
    driver starts. Returns its id, `run-<uuid>`, which the runner passes
    to the agent as `MUVUE_SESSION`."""
    session_id = f"run-{uuid.uuid4()}"
    now = _now_ts()
    real_cwd = os.path.realpath(cwd)
    with db_mod.write_txn(conn):
        conn.execute(
            "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, branch, started_at, "
            "last_event_at, last_action) VALUES (?, ?, ?, ?, 'launched', ?, ?, ?, ?, 'launched')",
            (session_id, project_id, node_id, agent, real_cwd, gitutil.branch_at(real_cwd), now, now),
        )
    return session_id


def set_pid(conn: sqlite3.Connection, session_id: str, pid: int) -> None:
    """The driver's process (and process group) id, the one thing `stop`
    signals. Only the runner calls this; no queue line can."""
    with db_mod.write_txn(conn):
        conn.execute("UPDATE sessions SET pid = ? WHERE id = ? AND origin = 'launched'", (pid, session_id))


def end_launched(conn: sqlite3.Connection, session_id: str) -> None:
    now = _now_ts()
    with db_mod.write_txn(conn):
        conn.execute(
            "UPDATE sessions SET ended_at = ?, waiting = 0, last_event_at = MAX(last_event_at, ?), "
            "last_action = 'run ended' WHERE id = ? AND ended_at IS NULL",
            (now, now, session_id),
        )
```

- [ ] **Step 5: Wrap the driver call in `run_node`**

In `src/muvue/core/runner.py`, add `import os` to the stdlib imports and `from . import sessions as sessions_mod` after `from . import runners as runners_mod`. Replace `:479-483`:

```python
    result = invoke(agent_name, agent_cfg, brief_text, Path(cwd), log_path=log_path)
    # The agent's commits reach the DB through the hook spool. Without a
    # daemon nothing drains it, so link them now: `done` judges the node
    # by its actual touches and diff.
    hooks_mod.drain_queue(conn, Path(repo_root))
```

with:

```python
    # Mission control phase 1: this run is a `launched` session from before
    # the agent starts until it returns. The agent's hooks name it through
    # MUVUE_SESSION, so its own session id folds into this row.
    session_id = sessions_mod.start_launched(
        conn, node_id=node["id"], project_id=node["project_id"], agent=agent_name, cwd=cwd,
    )
    try:
        result = invoke(
            agent_name, agent_cfg, brief_text, Path(cwd), log_path=log_path,
            env={**os.environ, sessions_mod.LAUNCHED_SESSION_ENV: session_id},
            on_spawn=lambda pid: sessions_mod.set_pid(conn, session_id, pid),
        )
    except BaseException:
        sessions_mod.end_launched(conn, session_id)
        raise
    # The agent's commits reach the DB through the hook spool. Without a
    # daemon nothing drains it, so link them now: `done` judges the node
    # by its actual touches and diff. Draining before ending the session
    # lets the agent's last lines land on a live row.
    hooks_mod.drain_queue(conn, Path(repo_root))
    sessions_mod.end_launched(conn, session_id)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_launched_sessions.py tests/test_runner_live_findings.py tests/test_runner.py tests/test_drivers.py tests/test_v4_rate_limit_wait_timeout.py tests/test_run_cli.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/drivers.py src/muvue/core/runner.py src/muvue/core/sessions.py tests/test_launched_sessions.py tests/test_runner_live_findings.py
git commit -F - <<'MSG'
Record each runner invocation as a launched session with its driver pid

The runner inserts the row before the driver starts, passes
MUVUE_SESSION to the agent, stores the pid from on_spawn, and ends the
session after draining the agent's own hook lines into it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 7: MCP session tools for other agent CLIs

**Files:**
- Modify: `src/muvue/core/sessions.py` (append `SessionError`, `spool_start`, `spool_heartbeat`, `spool_end`)
- Modify: `src/muvue/mcp_server.py:1-16` (docstring), `:106-118` (handlers, `TOOLS`), `:141-171` (`_call_tool`)
- Create: `tests/test_mcp_sessions.py`

**Interfaces:**
- Consumes: `apply_line` (Task 4) drains what these spool; `_hook._append_queue`, `_hook._now_iso` (Task 2).
- Produces:
  - `muvue.core.sessions.SessionError(Exception)`: a refused session request; the message says why (Task 9 maps it to HTTP 409).
  - `spool_start(repo_root: Path, *, agent: str, cwd: str, node_id: int | None = None) -> dict` returning `{"session_id": "mcp-<uuid>"}`
  - `spool_heartbeat(repo_root: Path, session_id: str, *, action: str | None = None, touched_paths: list[str] | None = None) -> dict`
  - `spool_end(repo_root: Path, session_id: str) -> dict`
  - MCP tools `session_start(agent, cwd, node_id?)`, `session_heartbeat(session_id, action?, touched_paths?)`, `session_end(session_id)`. Line shapes: `{"event": "session-start", "ts", "session_id", "agent", "cwd", ["bind_node_id"], ["launched_session"]}`, `{"event": "heartbeat", "ts", "session_id", ["action"], ["touched_paths"], ["launched_session"]}`, `{"event": "session-end", "ts", "session_id", ["launched_session"]}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_sessions.py`:

```python
"""Mission control phase 1: agent CLIs without Claude Code hooks report
sessions through three MCP tools, which spool the same queue lines the
hooks write."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import mcp_server
from muvue.core import db as core_db, hooks, nodes, projects
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


@pytest.fixture(autouse=True)
def _no_launched_env(monkeypatch):
    monkeypatch.delenv("MUVUE_SESSION", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _call(repo: Path, name: str, arguments: dict) -> dict:
    return mcp_server.handle_request(
        repo, MuvueConfig(),
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )


def _result(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def _drain(conn, repo: Path) -> None:
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def test_tools_list_names_the_session_tools(repo):
    response = mcp_server.handle_request(repo, MuvueConfig(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"session_start", "session_heartbeat", "session_end"} <= names


def test_session_tools_round_trip_through_the_drain(repo, conn):
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo)}))["session_id"]
    assert session_id.startswith("mcp-")
    _call(repo, "session_heartbeat", {"session_id": session_id, "action": "running tests", "touched_paths": ["src/a.py"]})
    _drain(conn, repo)
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert (row["agent"], row["origin"], row["last_action"], row["ended_at"]) == ("codex", "hand", "running tests", None)
    assert [r["path"] for r in conn.execute("SELECT path FROM session_touches")] == ["src/a.py"]
    _call(repo, "session_end", {"session_id": session_id})
    _drain(conn, repo)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()["ended_at"] is not None


def test_session_start_binds_an_explicit_node(repo, conn):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo), "node_id": node["id"]}))["session_id"]
    _drain(conn, repo)
    row = conn.execute("SELECT node_id, project_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert (row["node_id"], row["project_id"]) == (node["id"], project["id"])


def test_a_launched_agents_mcp_session_folds_into_its_run(repo, conn, monkeypatch):
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    conn.execute(
        "INSERT INTO sessions (id, project_id, node_id, agent, origin, cwd, started_at, last_event_at) "
        "VALUES ('run-9', ?, ?, 'codex', 'launched', ?, '2000-01-01T00:00:00.000Z', '2000-01-01T00:00:00.000Z')",
        (project["id"], node["id"], str(repo)),
    )
    monkeypatch.setenv("MUVUE_SESSION", "run-9")
    session_id = _result(_call(repo, "session_start", {"agent": "codex", "cwd": str(repo)}))["session_id"]
    _drain(conn, repo)
    assert conn.execute("SELECT alias_of FROM sessions WHERE id = ?", (session_id,)).fetchone()["alias_of"] == "run-9"


@pytest.mark.parametrize("name, arguments, code, text", [
    ("session_start", {"agent": "codex", "cwd": "relative"}, -32000, "absolute"),
    ("session_start", {"agent": "", "cwd": "/x"}, -32000, "agent"),
    ("session_start", {"agent": "codex", "cwd": "/x", "node_id": "7"}, -32000, "node_id"),
    ("session_heartbeat", {"session_id": "has space"}, -32000, "not a session id"),
    ("session_heartbeat", {"session_id": "s", "touched_paths": "a.py"}, -32000, "touched_paths"),
    ("session_heartbeat", {"action": "x"}, -32602, "session_id"),
])
def test_bad_arguments_are_json_rpc_errors(repo, name, arguments, code, text):
    response = _call(repo, name, arguments)
    assert response["error"]["code"] == code
    assert text in response["error"]["message"]
    assert not (repo / ".muvue" / "queue.jsonl").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_sessions.py -q`
Expected: FAIL. `tools/list` lacks the names; `tools/call` returns `{"error": {"code": -32601, "message": "unknown tool: 'session_start'"}}`, so `_result` raises `KeyError: 'result'`.

- [ ] **Step 3: Add the spooling functions to `src/muvue/core/sessions.py`**

Add `from pathlib import Path` to the imports and append:

```python
class SessionError(Exception):
    """A session request muvue refuses; the message says why."""


def _with_launched(line: dict) -> dict:
    launched = os.environ.get(LAUNCHED_SESSION_ENV)
    if launched:
        line["launched_session"] = launched
    return line


def _checked_id(session_id) -> str:
    if not _valid_id(session_id):
        raise SessionError(f"not a session id: {session_id!r}")
    return session_id


def spool_start(repo_root: Path, *, agent: str, cwd: str, node_id: int | None = None) -> dict:
    """MCP `session_start`, for agent CLIs without Claude Code hooks: the
    same queue line a SessionStart hook writes. Returns the new session's
    id (`mcp-<uuid>`), which later heartbeat and end calls name. An MCP
    server started by a launched agent inherits `MUVUE_SESSION`, so its
    session folds into the run like a hook's would."""
    if not isinstance(agent, str) or not agent.strip():
        raise SessionError("agent must be a non-empty string")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise SessionError(f"cwd must be an absolute path, got {cwd!r}")
    if node_id is not None and type(node_id) is not int:
        raise SessionError(f"node_id must be an integer, got {node_id!r}")
    session_id = f"mcp-{uuid.uuid4()}"
    line = {"event": "session-start", "ts": _hook._now_iso(), "session_id": session_id, "agent": agent.strip(), "cwd": cwd}
    if node_id is not None:
        line["bind_node_id"] = node_id
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}


def spool_heartbeat(
    repo_root: Path, session_id: str, *, action: str | None = None, touched_paths: list[str] | None = None,
) -> dict:
    """MCP `session_heartbeat`: the session is working; `action` becomes
    its last action and `touched_paths` (absolute, or relative to its
    directory) its touched files."""
    line = {"event": "heartbeat", "ts": _hook._now_iso(), "session_id": _checked_id(session_id)}
    if action is not None:
        if not isinstance(action, str):
            raise SessionError("action must be a string")
        line["action"] = action
    if touched_paths is not None:
        if not isinstance(touched_paths, list) or not all(isinstance(p, str) for p in touched_paths):
            raise SessionError("touched_paths must be a list of strings")
        line["touched_paths"] = touched_paths
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}


def spool_end(repo_root: Path, session_id: str) -> dict:
    """MCP `session_end`."""
    line = {"event": "session-end", "ts": _hook._now_iso(), "session_id": _checked_id(session_id)}
    _hook._append_queue(str(repo_root), _with_launched(line))
    return {"session_id": session_id}
```

- [ ] **Step 4: Expose the tools in `src/muvue/mcp_server.py`**

After `_status` (`:106-107`), add:

```python
def _session_start(conn: sqlite3.Connection, config, args: dict, repo_root: Path) -> dict:
    return core.sessions.spool_start(repo_root, agent=args["agent"], cwd=args["cwd"], node_id=args.get("node_id"))


def _session_heartbeat(conn: sqlite3.Connection, config, args: dict, repo_root: Path) -> dict:
    return core.sessions.spool_heartbeat(
        repo_root, args["session_id"], action=args.get("action"), touched_paths=args.get("touched_paths"),
    )


def _session_end(conn: sqlite3.Connection, config, args: dict, repo_root: Path) -> dict:
    return core.sessions.spool_end(repo_root, args["session_id"])
```

Add three entries at the end of `TOOLS` (after `"status": ...`):

```python
    "session_start": (_session_start, {"type": "object", "properties": {"agent": {"type": "string"}, "cwd": {"type": "string"}, "node_id": {"type": "integer"}}, "required": ["agent", "cwd"]}, "Tell muvue an agent session started in `cwd` (absolute path); returns its session_id. For agent CLIs without Claude Code hooks."),
    "session_heartbeat": (_session_heartbeat, {"type": "object", "properties": {"session_id": {"type": "string"}, "action": {"type": "string"}, "touched_paths": {"type": "array", "items": {"type": "string"}}}, "required": ["session_id"]}, "Report that a session is working: what it is doing and which files it touched."),
    "session_end": (_session_end, {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}, "Tell muvue a session ended."),
```

Add above `def _call_tool(`:

```python
# Handlers that take `repo_root` as a fourth argument: `start`/`done` for
# branch coherence, worktrees and checks; the session tools to spool.
_NEEDS_REPO_ROOT = ("start", "done", "session_start", "session_heartbeat", "session_end")
```

In `_call_tool`, replace the comment and condition

```python
            # `start` alone also needs repo_root (v4 section 5, branch
            # coherence -- "every start" -- and, pre-existing, strict-mode
            # worktree binding; every other verb's core.* call takes
            # (conn, ...) with no repo_root parameter at all).
            if name in ("start", "done"):
```

with:

```python
            if name in _NEEDS_REPO_ROOT:
```

and add `core.sessions.SessionError,` to the tuple of exceptions mapped to `-32000` (after `core.state_machine.NotLeaseOwner,`).

In the module docstring, change the first sentence to:

```
"""Minimal MCP stdio server (plan section 4/7): exposes *agent* verbs
only -- `brief/show/start/done/fail/note/ask/wait/replan/status`, plus
the session tools `session_start/session_heartbeat/session_end` (mission
control phase 1) -- never the human verbs (`approve/reject/ack/merge/close/pause/resume/
handoff/import`), per plan section 4's explicit split: "Never exposed
over MCP" for human verbs.
```

(keeping the rest of the docstring as it is).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_sessions.py tests/test_mcp_server.py tests/test_schema_v4.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/sessions.py src/muvue/mcp_server.py tests/test_mcp_sessions.py
git commit -F - <<'MSG'
Add MCP session_start, session_heartbeat and session_end tools

They spool the same queue lines the Claude Code hooks write, so any
agent CLI with MCP can report its sessions.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 8: Worktree listing, branch status cache and stages

**Files:**
- Modify: `src/muvue/core/gitutil.py` (append `GitError`, `Worktree`, `parse_worktree_porcelain`, `list_worktrees`, `rev_parse`, `BranchStatus`, `branch_status`)
- Modify: `src/muvue/core/sessions.py` (append `launched_run_finished`)
- Create: `src/muvue/core/worktrees.py`
- Modify: `src/muvue/core/__init__.py` (export `worktrees`)
- Create: `tests/test_worktrees.py`

**Interfaces:**
- Consumes: `list_sessions` (Task 5); `strict.airlock_path` (existing, `src/muvue/core/strict.py:52-53`).
- Produces:
  - `muvue.core.gitutil.GitError(Exception)`, message `git failed: <first line of stderr>`
  - `Worktree(path: str, head: str | None, branch: str | None, bare=False, detached=False, locked=False, prunable=False)` (frozen dataclass; `branch` is the short name)
  - `parse_worktree_porcelain(text: str) -> list[Worktree]`, `list_worktrees(repo_root: Path) -> list[Worktree]`, `rev_parse(repo_root: Path, ref: str) -> str`
  - `BranchStatus(ahead: int, behind: int, changed_paths: tuple[str, ...], fork_sha: str, fork_time: int, tip_time: int)`
  - `branch_status(repo_root: Path, *, base_sha: str, tip_sha: str) -> BranchStatus`, cached in `_STATUS_CACHE` by (resolved repo, tip sha, base sha), at most `_STATUS_CACHE_MAX = 256` entries
  - `muvue.core.sessions.launched_run_finished(conn, node_id: int) -> bool`
  - `muvue.core.worktrees.STAGES = ("worktree", "commits", "checks", "review", "merged")`, `worktree_stage(*, ahead, behind, node_status, checks_running) -> str`, `repo_worktrees(conn, repo_root: Path, config: MuvueConfig, *, now=None) -> {"worktrees": [row, ...], "errors": [str, ...]}`. Row keys: `path, branch, head, base, is_base, ahead, behind, changed_paths, fork_time, tip_time, merged, error, session_ids, stage, node_id, node_title, node_status, planned` (`planned` = `[{"glob", "touched"}]`, sorted by glob).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worktrees.py`:

```python
"""Mission control phase 1: `git worktree list --porcelain` parsing,
ahead/behind and changed paths per branch (cached by sha), and the rows
the Agents tab reads: base branch, stage, bound node, planned files,
live sessions. Real git fixtures throughout."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import git_init_with_commit
from muvue import _hook
from muvue.core import db as core_db, gitutil, hooks, nodes, projects, strict as strict_mod, worktrees
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

PORCELAIN = """worktree /r
HEAD aaaa
branch refs/heads/main

worktree /r/wt
HEAD bbbb
detached

worktree /r/locked
HEAD cccc
branch refs/heads/feat
locked reason here

worktree /bare.git
bare
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV).stdout


def _commit(cwd: Path, name: str) -> None:
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}\n")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"add {name}")


@pytest.fixture(autouse=True)
def _fresh_cache():
    gitutil._STATUS_CACHE.clear()
    yield
    gitutil._STATUS_CACHE.clear()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    return Path(os.path.realpath(root))


# -- gitutil ----------------------------------------------------------------


def test_parse_worktree_porcelain():
    assert gitutil.parse_worktree_porcelain(PORCELAIN) == [
        gitutil.Worktree(path="/r", head="aaaa", branch="main"),
        gitutil.Worktree(path="/r/wt", head="bbbb", branch=None, detached=True),
        gitutil.Worktree(path="/r/locked", head="cccc", branch="feat", locked=True),
        gitutil.Worktree(path="/bare.git", head=None, branch=None, bare=True),
    ]


def test_list_worktrees_reads_a_real_repo(repo, tmp_path):
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt"))
    _git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt2"))
    found = gitutil.list_worktrees(repo)
    assert [(os.path.realpath(w.path), w.branch, w.detached) for w in found] == [
        (str(repo), "main", False),
        (os.path.realpath(tmp_path / "wt"), "feat", False),
        (os.path.realpath(tmp_path / "wt2"), None, True),
    ]
    assert all(len(w.head) == 40 for w in found)


def test_list_worktrees_reports_the_git_error(tmp_path):
    with pytest.raises(gitutil.GitError, match=r"^git failed: fatal: not a git repository"):
        gitutil.list_worktrees(tmp_path)


def test_branch_status_counts_changes_and_caches_by_sha(repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    _commit(wt, "f.txt")
    _commit(repo, "m.txt")
    base_sha, tip_sha = gitutil.rev_parse(repo, "main"), gitutil.rev_parse(repo, "feat")
    calls: list[tuple] = []
    real = gitutil._git
    monkeypatch.setattr(gitutil, "_git", lambda root, *args: calls.append(args) or real(root, *args))

    status = gitutil.branch_status(repo, base_sha=base_sha, tip_sha=tip_sha)
    assert (status.ahead, status.behind, status.changed_paths) == (1, 1, ("f.txt",))
    assert status.fork_time <= status.tip_time
    spawned = len(calls)
    assert spawned > 0

    assert gitutil.branch_status(repo, base_sha=base_sha, tip_sha=tip_sha) == status
    assert len(calls) == spawned  # unchanged worktree: no git at all

    _commit(wt, "g.txt")
    moved = gitutil.branch_status(repo, base_sha=base_sha, tip_sha=gitutil.rev_parse(repo, "feat"))
    assert moved.ahead == 2


@pytest.mark.parametrize("kw, stage", [
    ({"ahead": 0, "behind": 0, "node_status": None, "checks_running": False}, "worktree"),
    ({"ahead": 2, "behind": 0, "node_status": None, "checks_running": False}, "commits"),
    ({"ahead": 2, "behind": 0, "node_status": "in_progress", "checks_running": True}, "checks"),
    ({"ahead": 2, "behind": 1, "node_status": "review", "checks_running": False}, "review"),
    ({"ahead": 0, "behind": 3, "node_status": None, "checks_running": False}, "merged"),
    ({"ahead": 0, "behind": 0, "node_status": "done", "checks_running": False}, "merged"),
])
def test_worktree_stage(kw, stage):
    assert worktrees.worktree_stage(**kw) == stage


# -- rows for the Agents tab --------------------------------------------------


def _spool(repo: Path, **line) -> None:
    line.setdefault("ts", _hook._now_iso())
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def test_rows_link_sessions_nodes_planned_files_and_stage(repo, tmp_path):
    init_repo(repo)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        project = projects.create_project(conn, goal="g")
        node = nodes.create_node(
            conn, project_id=project["id"], kind="task", title="Detect pitch", status="ready",
            predicted_touches=["web/*.ts", "core/*.py"],
        )
        wt = tmp_path / "node-wt"
        _git(repo, "worktree", "add", "-q", "-b", f"node-{node['id']}", str(wt))
        conn.execute("UPDATE nodes SET worktree = ? WHERE id = ?", (str(wt), node["id"]))
        _commit(wt, "core/pitch.py")
        _git(repo, "worktree", "add", "-q", "-b", "idle", str(tmp_path / "idle-wt"))
        _spool(repo, event="session-start", node_id=None, session_id="s-1", cwd=str(wt / "core"), agent="claude")
        _spool(repo, event="session-start", node_id=None, session_id="s-main", cwd=str(repo), agent="claude")
        hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()

    assert result["errors"] == []
    rows = {r["branch"]: r for r in result["worktrees"]}
    assert (rows["main"]["is_base"], rows["main"]["stage"], rows["main"]["session_ids"]) == (True, None, ["s-main"])
    row = rows[f"node-{node['id']}"]
    assert (row["ahead"], row["behind"], row["changed_paths"], row["stage"]) == (1, 0, ["core/pitch.py"], "commits")
    assert (row["node_id"], row["node_title"], row["session_ids"], row["base"]) == (node["id"], "Detect pitch", ["s-1"], "main")
    assert row["planned"] == [{"glob": "core/*.py", "touched": True}, {"glob": "web/*.ts", "touched": False}]
    assert (rows["idle"]["session_ids"], rows["idle"]["stage"], rows["idle"]["merged"]) == ([], "worktree", False)


def test_rows_include_the_strict_mode_airlock(repo, tmp_path, monkeypatch):
    init_repo(repo)
    airlock = tmp_path / "airlock.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(airlock))
    _git(airlock, "worktree", "add", "-q", "-b", "node-5", str(tmp_path / "node-5"), "main")
    monkeypatch.setattr(strict_mod, "airlock_path", lambda root: airlock)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()
    row = next(r for r in result["worktrees"] if r["branch"] == "node-5")
    assert (row["base"], row["is_base"], row["stage"], row["error"]) == ("main", False, "worktree", None)


def test_a_git_failure_lands_on_its_row(repo, tmp_path, monkeypatch):
    init_repo(repo)
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "wt"))

    def failing(*args, **kwargs):
        raise gitutil.GitError("git failed: boom")

    monkeypatch.setattr(gitutil, "branch_status", failing)
    conn = core_db.connect(repo / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, repo, MuvueConfig())
    finally:
        conn.close()
    feat = next(r for r in result["worktrees"] if r["branch"] == "feat")
    assert (feat["error"], feat["stage"]) == ("git failed: boom", None)


def test_a_listing_failure_is_reported_not_raised(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    init_repo(plain)
    conn = core_db.connect(plain / ".muvue" / "muvue.db")
    try:
        result = worktrees.repo_worktrees(conn, plain, MuvueConfig())
    finally:
        conn.close()
    assert result["worktrees"] == []
    assert len(result["errors"]) == 1 and "git failed: " in result["errors"][0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_worktrees.py -q`
Expected: FAIL at collection: `ImportError: cannot import name 'worktrees' from 'muvue.core'`.

- [ ] **Step 3: Append the git readers to `src/muvue/core/gitutil.py`**

Add `from dataclasses import dataclass` to the imports and append:

```python
class GitError(Exception):
    """A git command failed. The message is `git failed: <first line of
    stderr>`, shown as is on the dashboard row it concerns."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=Path(repo_root), capture_output=True, text=True)
    except OSError as e:
        raise GitError(f"git failed: {e}") from e
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise GitError(f"git failed: {lines[0] if lines else f'exit {result.returncode}'}")
    return result.stdout


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str | None
    branch: str | None  # short name; None when detached or bare
    bare: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False


def parse_worktree_porcelain(text: str) -> list[Worktree]:
    """`git worktree list --porcelain`: blank-line separated records of
    `key value` lines (`worktree`, `HEAD`, `branch`) and bare flags
    (`bare`, `detached`, `locked [reason]`, `prunable [reason]`)."""
    found = []
    for block in text.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        found.append(Worktree(
            path=fields["worktree"], head=fields.get("HEAD"),
            branch=branch.removeprefix("refs/heads/") if branch else None,
            bare="bare" in fields, detached="detached" in fields,
            locked="locked" in fields, prunable="prunable" in fields,
        ))
    return found


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """Every worktree of the repository at `repo_root` (a checkout or a
    bare repository), main worktree first."""
    return parse_worktree_porcelain(_git(repo_root, "worktree", "list", "--porcelain"))


def rev_parse(repo_root: Path, ref: str) -> str:
    return _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


@dataclass(frozen=True)
class BranchStatus:
    ahead: int
    behind: int
    changed_paths: tuple[str, ...]
    fork_sha: str
    fork_time: int  # unix seconds
    tip_time: int


_STATUS_CACHE: dict[tuple[str, str, str], BranchStatus] = {}
_STATUS_CACHE_MAX = 256


def branch_status(repo_root: Path, *, base_sha: str, tip_sha: str) -> BranchStatus:
    """Commits ahead of and behind the base, the paths changed since the
    fork point (`git diff --name-only base...tip`), and the fork and tip
    commit times. Keyed by sha, not branch name: an unchanged worktree is
    answered from the cache without spawning git, so a dashboard refresh
    costs nothing for branches that did not move."""
    key = (str(Path(repo_root).resolve()), tip_sha, base_sha)
    cached = _STATUS_CACHE.get(key)
    if cached is not None:
        return cached
    behind, ahead = (int(n) for n in _git(repo_root, "rev-list", "--left-right", "--count", f"{base_sha}...{tip_sha}").split())
    changed = tuple(p for p in _git(repo_root, "diff", "--name-only", f"{base_sha}...{tip_sha}").splitlines() if p)
    fork_sha = _git(repo_root, "merge-base", base_sha, tip_sha).strip()
    fork_time = int(_git(repo_root, "log", "-1", "--format=%ct", fork_sha).strip())
    tip_time = int(_git(repo_root, "log", "-1", "--format=%ct", tip_sha).strip())
    status = BranchStatus(ahead, behind, changed, fork_sha, fork_time, tip_time)
    if len(_STATUS_CACHE) >= _STATUS_CACHE_MAX:
        _STATUS_CACHE.pop(next(iter(_STATUS_CACHE)))
    _STATUS_CACHE[key] = status
    return status
```

- [ ] **Step 4: Add `launched_run_finished` to `src/muvue/core/sessions.py`**

Append:

```python
def launched_run_finished(conn: sqlite3.Connection, node_id: int) -> bool:
    """Whether the node's latest launched session has ended. While the
    node is still `in_progress`, that means the runner is past the agent
    and inside `done`, running the checks: the "checks" stage."""
    row = db_mod.query_one(
        conn,
        "SELECT ended_at FROM sessions WHERE node_id = ? AND origin = 'launched' AND alias_of IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (node_id,),
    )
    return row is not None and row["ended_at"] is not None
```

- [ ] **Step 5: Create `src/muvue/core/worktrees.py`**

```python
"""Worktrees of this repository for the dashboard's Agents tab (mission
control phase 1): every worktree of the checkout plus the strict-mode
airlock's node worktrees (`git worktree list` in the checkout does not
show those), each with ahead/behind against its base branch, changed
paths, the node bound to it, its planned files, the live sessions in it,
and a stage.

A git failure never fails the page: one for a whole listing goes in
`errors`, one for a branch goes on that row as `git failed: ...`."""

from __future__ import annotations

import fnmatch
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db as db_mod
from . import gitutil
from . import sessions as sessions_mod
from . import strict as strict_mod
from .config import MuvueConfig

STAGES = ("worktree", "commits", "checks", "review", "merged")


def worktree_stage(*, ahead: int, behind: int, node_status: str | None, checks_running: bool) -> str:
    """Where a branch is on its way to the base: no commits yet, commits,
    node checks running, node in review, merged. git cannot tell a branch
    merged by fast-forward from one created at an older base commit with
    nothing on it; both read as merged."""
    if ahead == 0 and (behind > 0 or node_status == "done"):
        return "merged"
    if node_status == "review":
        return "review"
    if checks_running:
        return "checks"
    if ahead > 0:
        return "commits"
    return "worktree"


def _planned_globs(conn: sqlite3.Connection, node_id: int) -> list[str]:
    return [
        r["path_glob"] for r in db_mod.query_all(
            conn, "SELECT path_glob FROM predicted_touches WHERE node_id = ? ORDER BY path_glob", (node_id,),
        )
    ]


def _row(conn, entry: gitutil.Worktree, *, git_dir: Path, base: str | None, base_sha: str | None,
         base_error: str | None, is_base: bool, node) -> dict:
    row = {
        "path": entry.path, "branch": entry.branch, "head": entry.head, "base": base, "is_base": is_base,
        "ahead": None, "behind": None, "changed_paths": [], "fork_time": None, "tip_time": None,
        "merged": False, "error": None, "session_ids": [], "stage": None,
        "node_id": node["id"] if node else None, "node_title": node["title"] if node else None,
        "node_status": node["status"] if node else None, "planned": [],
    }
    globs = _planned_globs(conn, node["id"]) if node else []
    row["planned"] = [{"glob": g, "touched": False} for g in globs]
    if is_base:
        return row
    if base_error is not None or entry.head is None:
        row["error"] = base_error or "git failed: the worktree has no HEAD"
        return row
    try:
        status = gitutil.branch_status(git_dir, base_sha=base_sha, tip_sha=entry.head)
    except gitutil.GitError as e:
        row["error"] = str(e)
        return row
    checks_running = node is not None and node["status"] == "in_progress" and sessions_mod.launched_run_finished(conn, node["id"])
    stage = worktree_stage(ahead=status.ahead, behind=status.behind, node_status=row["node_status"], checks_running=checks_running)
    row.update(
        ahead=status.ahead, behind=status.behind, changed_paths=list(status.changed_paths),
        fork_time=status.fork_time, tip_time=status.tip_time, stage=stage, merged=stage == "merged",
        planned=[{"glob": g, "touched": any(fnmatch.fnmatch(p, g) for p in status.changed_paths)} for g in globs],
    )
    return row


def _attach_sessions(rows: list[dict], live: list[dict]) -> None:
    """Each live session goes to the deepest worktree containing its
    directory (a worktree inside the checkout beats the checkout)."""
    tops = sorted(((os.path.realpath(r["path"]), r) for r in rows), key=lambda t: len(t[0]), reverse=True)
    for session in live:
        cwd = os.path.realpath(session["cwd"])
        for top, row in tops:
            if cwd == top or cwd.startswith(top + os.sep):
                row["session_ids"].append(session["id"])
                break


def repo_worktrees(conn: sqlite3.Connection, repo_root: Path, config: MuvueConfig, *, now: datetime | None = None) -> dict:
    live = sessions_mod.list_sessions(conn, config.sessions, now=now)
    nodes_by_path = {
        os.path.realpath(r["worktree"]): r for r in db_mod.query_all(
            conn, "SELECT id, title, status, worktree FROM nodes WHERE worktree IS NOT NULL AND deleted_at IS NULL",
        )
    }
    sources: list[tuple[Path, str | None]] = [(Path(repo_root), None)]
    airlock = strict_mod.airlock_path(repo_root)
    if airlock.exists():
        sources.append((airlock, "main"))
    rows: list[dict] = []
    errors: list[str] = []
    for git_dir, base in sources:
        try:
            entries = [e for e in gitutil.list_worktrees(git_dir) if not e.bare]
        except gitutil.GitError as e:
            errors.append(f"{git_dir}: {e}")
            continue
        is_checkout = base is None
        if is_checkout:
            # The main worktree is listed first; its branch is the base.
            base = entries[0].branch if entries else None
        base_sha, base_error = None, None
        if base is None:
            base_error = "git failed: the base checkout is on a detached HEAD"
        else:
            try:
                base_sha = gitutil.rev_parse(git_dir, base)
            except gitutil.GitError as e:
                base_error = str(e)
        for i, entry in enumerate(entries):
            rows.append(_row(
                conn, entry, git_dir=git_dir, base=base, base_sha=base_sha, base_error=base_error,
                is_base=is_checkout and i == 0, node=nodes_by_path.get(os.path.realpath(entry.path)),
            ))
    _attach_sessions(rows, live)
    return {"worktrees": rows, "errors": errors}
```

In `src/muvue/core/__init__.py`, add `worktrees,` after `trailers,` in the `from . import (...)` list and `"worktrees",` after `"trailers",` in `__all__`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_worktrees.py tests/test_branch_coherence.py tests/test_strict_mode.py tests/test_txn_discipline.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/muvue/core/gitutil.py src/muvue/core/sessions.py src/muvue/core/worktrees.py src/muvue/core/__init__.py tests/test_worktrees.py
git commit -F - <<'MSG'
List worktrees with ahead/behind, changed paths, node, sessions and stage

gitutil parses git worktree list --porcelain and caches branch status by
sha, so an unchanged worktree costs no git call. The strict-mode
airlock's worktrees are listed too. git failures land on the row.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 9: API: `GET /sessions`, `GET /worktrees`, `POST /sessions/{id}/stop`

**Files:**
- Modify: `src/muvue/core/sessions.py` (append `get_session`, `stop_launched`)
- Modify: `src/muvue/api/app.py:271-293` (`_handle_core_error`), after `:387` (new routes)
- Create: `tests/test_api_sessions.py`

**Interfaces:**
- Consumes: `list_sessions` (Task 5), `start_launched`/`set_pid`/`end_launched` (Task 6), `SessionError` (Task 7), `repo_worktrees` (Task 8).
- Produces:
  - `muvue.core.sessions.get_session(conn, session_id: str) -> sqlite3.Row` (resolves an alias to its run; `LookupError` if unknown)
  - `muvue.core.sessions.stop_launched(conn, session_id: str, cfg: SessionsConfig, *, actor_evidence: str, now: datetime | None = None, kill=os.killpg) -> dict` returning `{"session_id", "signalled": pid}`; raises `SessionError("started by hand; stop it where it runs")` for a hand session, `SessionError` for an ended session or one with no pid; records event `session.stopped`.
  - `GET /sessions?project_id=&include_ended=0` -> `{"sessions": [...]}` (Task 5 dict shape)
  - `GET /worktrees` -> `{"worktrees": [...], "errors": [...]}` (Task 8 row shape)
  - `POST /sessions/{session_id}/stop` -> 200 `{"session_id", "signalled"}`, 404 unknown, 409 with the reason
  - All three call `_require_session`; the POST is JSON-only like every other.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_sessions.py`:

```python
"""Mission control phase 1: the session and worktree read views, and
Stop for sessions muvue launched (never for hand-started ones)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import git_init_with_commit
from muvue import _hook
from muvue.api import create_app
from muvue.core import db as core_db, hooks, nodes, projects, sessions
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_init_with_commit(root)
    init_repo(root)
    return Path(os.path.realpath(root))


@pytest.fixture
def app(repo):
    return create_app(repo, config=MuvueConfig(checks=ChecksConfig(test="true", lint="true")))


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def auth(app) -> dict:
    return {"Authorization": f"Bearer {app.state.session.token}"}


@pytest.fixture
def conn(repo):
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


def _hand_session(conn, repo: Path, session_id: str = "s-1", *events: str) -> None:
    with open(repo / ".muvue" / "queue.jsonl", "a") as f:
        for event in ("session-start", *events):
            f.write(json.dumps({"event": event, "ts": _hook._now_iso(), "node_id": None, "session_id": session_id,
                                "cwd": str(repo), "agent": "claude"}) + "\n")
    hooks.drain_queue(conn, repo, max_items=1000, max_seconds=60)


def _launched(conn, repo: Path) -> str:
    project = projects.create_project(conn, goal="g")
    node = nodes.create_node(conn, project_id=project["id"], kind="task", title="t", status="ready")
    return sessions.start_launched(conn, node_id=node["id"], project_id=project["id"], agent="claude", cwd=str(repo))


def test_the_views_need_a_session(client):
    assert client.get("/sessions").status_code == 403
    assert client.get("/worktrees").status_code == 403
    assert client.post("/sessions/s-1/stop", json={}).status_code == 403


def test_get_sessions_lists_live_sessions_with_their_state(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    _hand_session(conn, repo, "s-2", "session-end")
    body = client.get("/sessions", headers=auth).json()
    assert [(s["id"], s["state"], s["origin"]) for s in body["sessions"]] == [("s-1", "working", "hand")]
    every = client.get("/sessions?include_ended=1", headers=auth).json()
    assert {s["id"] for s in every["sessions"]} == {"s-1", "s-2"}


def test_get_worktrees_lists_the_checkout(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    body = client.get("/worktrees", headers=auth).json()
    assert body["errors"] == []
    assert [(w["branch"], w["is_base"], w["session_ids"]) for w in body["worktrees"]] == [("main", True, ["s-1"])]


def test_stop_refuses_a_hand_session(client, auth, conn, repo):
    _hand_session(conn, repo, "s-1")
    response = client.post("/sessions/s-1/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"] == "started by hand; stop it where it runs"


def test_stop_of_an_unknown_session_is_404(client, auth):
    assert client.post("/sessions/nope/stop", json={}, headers=auth).status_code == 404


def test_stop_must_be_json(client, auth):
    response = client.post(
        "/sessions/s-1/stop", content="a=b",
        headers={**auth, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 403


def test_stop_signals_a_launched_sessions_process_group(client, auth, conn, repo):
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_id = _launched(conn, repo)
        sessions.set_pid(conn, run_id, proc.pid)
        response = client.post(f"/sessions/{run_id}/stop", json={}, headers=auth)
        assert response.status_code == 200
        assert response.json() == {"session_id": run_id, "signalled": proc.pid}
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'session.stopped'").fetchone()[0] == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_stop_reaches_the_run_through_the_agents_own_id(client, auth, conn, repo):
    run_id = _launched(conn, repo)
    conn.execute(
        "INSERT INTO sessions (id, agent, origin, cwd, started_at, last_event_at, alias_of) "
        "VALUES ('claude-own', 'claude', 'launched', ?, 't', 't', ?)", (str(repo), run_id),
    )
    response = client.post("/sessions/claude-own/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert "has no process yet" in response.json()["detail"]


def test_stop_of_an_ended_run_is_refused(client, auth, conn, repo):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, 999999)
    sessions.end_launched(conn, run_id)
    response = client.post(f"/sessions/{run_id}/stop", json={}, headers=auth)
    assert response.status_code == 409
    assert "has already ended" in response.json()["detail"]


def test_stop_of_a_vanished_process_marks_the_run_ended(conn, repo):
    run_id = _launched(conn, repo)
    sessions.set_pid(conn, run_id, 4242)

    def gone(pid, sig):
        raise ProcessLookupError(pid)

    with pytest.raises(sessions.SessionError, match="no longer running"):
        sessions.stop_launched(conn, run_id, MuvueConfig().sessions, actor_evidence="tty", kill=gone)
    assert conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (run_id,)).fetchone()["ended_at"] is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_sessions.py -q`
Expected: FAIL. The routes return 404 (`assert 404 == 403` in the first test); `sessions.stop_launched` raises `AttributeError`.

- [ ] **Step 3: Add `get_session` and `stop_launched` to `src/muvue/core/sessions.py`**

Add `import signal` to the imports, and `from . import events as events_mod` after `from . import db as db_mod`. Append:

```python
def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    """One session's row; an alias (a launched agent's own id) resolves
    to its run. Raises LookupError for an unknown id."""
    row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (session_id,))
    if row is not None and row["alias_of"] is not None:
        row = db_mod.query_one(conn, "SELECT * FROM sessions WHERE id = ?", (row["alias_of"],))
    if row is None:
        raise LookupError(f"no such session: {session_id}")
    return row


def stop_launched(
    conn: sqlite3.Connection,
    session_id: str,
    cfg: SessionsConfig,
    *,
    actor_evidence: str,
    now: datetime | None = None,
    kill=os.killpg,
) -> dict:
    """SIGTERM a launched session's process group (the driver runs in its
    own session, so its pid is the group id -- as `drivers.stop_all`
    does). The runner then sees the driver exit and ends the row itself.
    A hand-started session is refused: muvue only watches those."""
    row = get_session(conn, session_id)
    if row["origin"] != "launched":
        raise SessionError("started by hand; stop it where it runs")
    if session_state(row, now or datetime.now(timezone.utc), cfg) == "ended":
        raise SessionError(f"session {row['id']} has already ended")
    if row["pid"] is None:
        raise SessionError(f"session {row['id']} has no process yet")
    try:
        kill(row["pid"], signal.SIGTERM)
    except ProcessLookupError:
        end_launched(conn, row["id"])
        raise SessionError(
            f"session {row['id']}'s process {row['pid']} is no longer running; marked it ended"
        ) from None
    with db_mod.write_txn(conn):
        events_mod.record_event(
            conn, project_id=row["project_id"], node_id=row["node_id"], actor="human",
            actor_evidence=actor_evidence, type_="session.stopped",
            payload={"session_id": row["id"], "pid": row["pid"]},
        )
    return {"session_id": row["id"], "signalled": row["pid"]}
```

- [ ] **Step 4: Add the routes to `src/muvue/api/app.py`**

In `_handle_core_error`, add `core.sessions.SessionError,` to the 409 tuple (after `core.removal.RemovalError,`).

After the `agents_status` route (ends at `:387`), add:

```python
    # ------------------------------------------------------------------
    # Sessions and worktrees (mission control phase 1). Gated like
    # /agents/status: rows carry working directories and transcript paths.
    # ------------------------------------------------------------------

    @app.get("/sessions")
    def list_sessions(request: Request, project_id: int | None = None, include_ended: int = 0) -> dict:
        """Every agent session with its computed state, node, worktree,
        branch, last action and spend (`core.sessions.list_sessions`)."""
        _require_session(request)
        with _conn() as conn:
            rows = core.sessions.list_sessions(
                conn, config.sessions, project_id=project_id, include_ended=bool(include_ended),
            )
        return {"sessions": rows}

    @app.get("/worktrees")
    def list_worktrees(request: Request) -> dict:
        """Every worktree of this repository and of its strict-mode airlock
        (`core.worktrees.repo_worktrees`). git failures are reported in the
        body, on the row or in `errors`, never as an HTTP error."""
        _require_session(request)
        with _conn() as conn:
            return core.worktrees.repo_worktrees(conn, repo_root, config)

    @app.post("/sessions/{session_id}/stop")
    def stop_session(request: Request, session_id: str) -> dict:
        """Stop a session muvue launched. A hand-started session gets 409:
        muvue never stops what a person started."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "session.stop",
                    lambda: core.sessions.stop_launched(
                        conn, session_id, config.sessions, actor_evidence="dashboard_token",
                    ),
                    atomic=False,
                )
            except Exception as e:
                _handle_core_error(e)
        return result
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_sessions.py tests/test_api.py tests/test_w9_security.py tests/test_daemon_security.py tests/test_dashboard_static.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/sessions.py src/muvue/api/app.py tests/test_api_sessions.py
git commit -F - <<'MSG'
Serve sessions and worktrees, and stop launched sessions over the API

GET /sessions and GET /worktrees are session-gated read views.
POST /sessions/{id}/stop sends SIGTERM to a launched session's process
group and refuses a hand-started session with 409.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 10: Dashboard routes and the project tab shell

**Files:**
- Modify: `dashboard/src/api/routes.ts:43-44` (three routes)
- Modify: `dashboard/src/router.ts` (append tab helpers)
- Create: `dashboard/src/project/ProjectTabs.tsx`
- Modify: `dashboard/src/app.tsx`
- Modify: `dashboard/src/shell/shell.css` (append `.project-tabs`)
- Modify: `dashboard/test/routes.test.ts`, `dashboard/test/no-stray-fetch.test.ts:24`
- Create: `dashboard/test/projectTabs.test.tsx`
- Modify (build output): `src/muvue/api/static/index.html`

**Interfaces:**
- Consumes: the API paths from Task 9.
- Produces:
  - `routes.sessions(projectId: number | null, includeEnded = false)`, `routes.worktrees()`, `routes.sessionStop(id: string)`
  - `type ProjectTab = "map" | "plan" | "agents" | "repo"`, `projectTabOf(r: Route): ProjectTab`, `setProjectTab(tab: ProjectTab): void` in `dashboard/src/router.ts` (the tab is the `tab` query parameter; `map` is the default and is never written)
  - `ProjectTabs({ tab })` and `TabPlaceholder({ text })`, `PLACEHOLDER_TEXT: Record<"plan" | "repo", string>` in `dashboard/src/project/ProjectTabs.tsx`
  - Task 11 replaces the Agents placeholder in `app.tsx` with `<AgentsTab projectId={projectId.value} />`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/test/routes.test.ts`:

```ts
test("session and worktree routes are spelled correctly", () => {
  expect(routes.sessions(3)).toBe("/sessions?project_id=3");
  expect(routes.sessions(null, true)).toBe("/sessions?include_ended=1");
  expect(routes.worktrees()).toBe("/worktrees");
  expect(routes.sessionStop("run-1")).toBe("/sessions/run-1/stop");
  expect(routes.sessionStop("a/b")).toBe("/sessions/a%2Fb/stop");
});
```

In `dashboard/test/no-stray-fetch.test.ts:24`, extend the path pattern so the guard also covers the new paths:

```ts
    if (/["`]\/(nodes|projects|events|questions|inbox|kpis|graph|auth|healthz|agents|status|brief|sessions|worktrees)\b/.test(text)) offenders.push(f);
```

Create `dashboard/test/projectTabs.test.tsx`:

```tsx
import { render, screen, fireEvent, within } from "@testing-library/preact";
import { App } from "../src/app";
import { parseHash, projectTabOf, setProjectTab, resetRouteFromLocation } from "../src/router";
import { projects, projectId } from "../src/state";

afterEach(() => { vi.restoreAllMocks(); });

test("the tab comes from ?tab= and defaults to the map", () => {
  expect(projectTabOf(parseHash("#/p/3"))).toBe("map");
  expect(projectTabOf(parseHash("#/p/3?tab=agents"))).toBe("agents");
  expect(projectTabOf(parseHash("#/p/3?tab=bogus"))).toBe("map");
});

test("setProjectTab keeps the page and the open node, and never writes the default", () => {
  window.location.hash = "#/p/3?node=7";
  setProjectTab("agents");
  expect(window.location.hash).toBe("#/p/3?node=7&tab=agents");
  setProjectTab("map");
  expect(window.location.hash).toBe("#/p/3?node=7");
});

test("a project has four tabs and Plan is a placeholder", () => {
  projects.value = [{ id: 4, goal: "g", phase: "executing" }];
  projectId.value = 4;
  window.location.hash = "#/p/4?tab=plan";
  resetRouteFromLocation();
  render(<App />);
  const nav = within(screen.getByRole("navigation", { name: "project views" }));
  expect(nav.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Map", "Plan", "Agents", "Repo"]);
  expect(nav.getByRole("tab", { name: "Plan" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(/Plan view is not built yet/)).toBeTruthy();
  fireEvent.click(nav.getByRole("tab", { name: "Repo" }));
  expect(window.location.hash).toBe("#/p/4?tab=repo");
});

test("with no project there are no tabs", () => {
  projects.value = [];
  projectId.value = null;
  window.location.hash = "#/";
  resetRouteFromLocation();
  render(<App />);
  expect(screen.queryByRole("navigation", { name: "project views" })).toBeNull();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dashboard && npx vitest run test/routes.test.ts test/projectTabs.test.tsx`
Expected: FAIL. `routes.sessions is not a function`; `projectTabOf` is not exported from `../src/router`.

- [ ] **Step 3: Add the routes**

In `dashboard/src/api/routes.ts`, add after `projectLogs: ...` (the last entry):

```ts
  sessions: (projectId: number | null, includeEnded = false) => q("/sessions", { project_id: projectId, include_ended: includeEnded ? 1 : null }),
  worktrees: () => "/worktrees",
  sessionStop: (id: string) => `/sessions/${encodeURIComponent(id)}/stop`,
```

- [ ] **Step 4: Add the tab helpers to `dashboard/src/router.ts`**

Append:

```ts
// Mission control: a project has four views. The view is a query
// parameter, not a path, because "#/plan" is a legacy route above.
export type ProjectTab = "map" | "plan" | "agents" | "repo";
const PROJECT_TABS: ProjectTab[] = ["map", "plan", "agents", "repo"];

export function projectTabOf(r: Route): ProjectTab {
  const tab = r.query.get("tab");
  return PROJECT_TABS.includes(tab as ProjectTab) ? (tab as ProjectTab) : "map";
}

export function setProjectTab(tab: ProjectTab): void {
  const r = parseHash(window.location.hash);
  const q = new URLSearchParams(r.query);
  if (tab === "map") q.delete("tab");
  else q.set("tab", tab);
  const qs = q.toString();
  navigate("#/" + [r.page, ...r.params].join("/") + (qs ? "?" + qs : ""));
}
```

- [ ] **Step 5: Create `dashboard/src/project/ProjectTabs.tsx`**

```tsx
import { Segmented } from "../ui/Segmented";
import { Empty } from "../ui/Empty";
import { setProjectTab, type ProjectTab } from "../router";

const TABS: { value: ProjectTab; label: string }[] = [
  { value: "map", label: "Map" },
  { value: "plan", label: "Plan" },
  { value: "agents", label: "Agents" },
  { value: "repo", label: "Repo" },
];

export const PLACEHOLDER_TEXT: Record<"plan" | "repo", string> = {
  plan: "Plan view is not built yet. The Map tab shows the plan.",
  repo: "Repo view is not built yet. The Agents tab lists branches and worktrees.",
};

export function ProjectTabs({ tab }: { tab: ProjectTab }) {
  return (
    <nav class="project-tabs" aria-label="project views">
      <Segmented options={TABS} value={tab} onChange={(v) => setProjectTab(v as ProjectTab)} />
    </nav>
  );
}

export function TabPlaceholder({ text }: { text: string }) {
  return <div class="page"><Empty text={text} /></div>;
}
```

- [ ] **Step 6: Render the selected tab in `dashboard/src/app.tsx`**

Change the imports at the top to:

```tsx
import { useEffect, useState } from "preact/hooks";
import { route, navigate, isLegacyRoute, projectTabOf } from "./router";
import { currentProject, projectId, signInOpen } from "./state";
import { Sidebar } from "./shell/Sidebar";
import { TopBar } from "./shell/TopBar";
import { AuthStrip } from "./shell/AuthStrip";
import { SignInSheet } from "./shell/SignInSheet";
import { Toasts } from "./ui/Toasts";
import { ProjectPage } from "./project/ProjectPage";
import { ProjectTabs, TabPlaceholder, PLACEHOLDER_TEXT } from "./project/ProjectTabs";
import { NodeSheet } from "./node/NodeSheet";
import { CommandPalette } from "./shell/CommandPalette";
```

Replace `const nodeId = route.value.query.get("node");` with:

```tsx
  const nodeId = route.value.query.get("node");
  const tab = projectTabOf(route.value);
  const hasProject = currentProject.value !== null;
```

and replace `<main><ProjectPage /></main>` with:

```tsx
        <main>
          {hasProject ? <ProjectTabs tab={tab} /> : null}
          {!hasProject || tab === "map" ? <ProjectPage /> : tab === "agents" ? (
            <TabPlaceholder text="Agents view is loading." />
          ) : (
            <TabPlaceholder text={PLACEHOLDER_TEXT[tab]} />
          )}
        </main>
```

(Task 11 replaces the Agents placeholder with the real tab.)

- [ ] **Step 7: Style the tab bar**

Append to `dashboard/src/shell/shell.css`:

```css
.project-tabs { padding: 8px 16px 0; overflow-x: auto; }
```

(The `.segmented button` rule in `dashboard/src/ui/ui.css:22` already gives each tab `min-height: 44px`.)

- [ ] **Step 8: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test`
Expected: typecheck exits 0; vitest reports every file passing, including `routes.test.ts`, `projectTabs.test.tsx`, `app.test.tsx`, `smoke.test.tsx` and `no-stray-fetch.test.ts`.

- [ ] **Step 9: Build and check the committed page serves every route**

Run: `cd dashboard && npm run build && cd .. && uv run pytest tests/test_dashboard_static.py -q`
Expected: the build writes `src/muvue/api/static/index.html`; the static tests PASS (`/sessions`, `/worktrees` and `/sessions/0/stop` are served since Task 9).

- [ ] **Step 10: Commit**

```bash
git add dashboard/src/api/routes.ts dashboard/src/router.ts dashboard/src/project/ProjectTabs.tsx dashboard/src/app.tsx dashboard/src/shell/shell.css dashboard/test/routes.test.ts dashboard/test/no-stray-fetch.test.ts dashboard/test/projectTabs.test.tsx src/muvue/api/static/index.html
git commit -F - <<'MSG'
Add Map, Plan, Agents and Repo tabs to the project page

The tab lives in the ?tab= query so the legacy #/plan route keeps
redirecting to the canvas. Plan and Repo are placeholders until later
phases fill them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 11: The Agents tab

**Files:**
- Create: `dashboard/src/agents/agentsData.ts`
- Create: `dashboard/src/agents/AgentsTab.tsx`
- Create: `dashboard/src/agents/BranchPanel.tsx`
- Create: `dashboard/src/agents/agents.css`
- Modify: `dashboard/src/main.tsx:6` (import the stylesheet)
- Modify: `dashboard/src/app.tsx` (render `AgentsTab`)
- Modify: `dashboard/test/touch-targets.test.ts` (append)
- Create: `dashboard/test/agentsFixtures.ts`, `dashboard/test/agentsData.test.ts`, `dashboard/test/AgentsTab.test.tsx`
- Modify (build output): `src/muvue/api/static/index.html`

**Interfaces:**
- Consumes: `routes.sessions`, `routes.worktrees`, `routes.sessionStop` (Task 10); the JSON shapes of Tasks 5, 8 and 9.
- Produces (in `dashboard/src/agents/agentsData.ts`):
  - types `SessionState`, `Session`, `Stage`, `Worktree`, `WorktreeList`, `BranchRow = { worktree: Worktree; session: Session | null }`
  - `STAGES`, `STATE_CLASS: Record<SessionState, string>` (pill class per state), `stateLabel(s)`, `isLive(s)`, `branchRows(list, sessions): { base: BranchRow | null; active: BranchRow[]; idle: BranchRow[] }`, `stageReached(stage)`, `taskLabel(w)`, `changesLabel(w)`, `aheadBehindLabel(w)`, `ageLabel(at, now)`, `spendLabel(spend)`
  - `AgentsTab({ projectId })`, `BranchPanel({ row, onClose })`

- [ ] **Step 1: Write the failing tests**

Create `dashboard/test/agentsFixtures.ts` (a plain module, not a test file, so importing it never re-runs tests):

```ts
import type { Session, Worktree } from "../src/agents/agentsData";

export function wt(over: Partial<Worktree>): Worktree {
  return {
    path: "/w", branch: "b", head: "h", base: "main", is_base: false, ahead: 0, behind: 0, changed_paths: [],
    fork_time: null, tip_time: null, merged: false, error: null, session_ids: [], node_id: null, node_title: null,
    node_status: null, stage: "worktree", planned: [], ...over,
  };
}

export function session(over: Partial<Session>): Session {
  return {
    id: "s", agent: "claude", origin: "hand", node_id: null, node_title: null, node_status: null, cwd: "/w",
    worktree: "/w", branch: "b", transcript_path: null, started_at: "2026-10-10T11:00:00.000Z",
    last_event_at: "2026-10-10T11:59:00.000Z", last_action: null, ended_at: null, waiting: false,
    state: "working", spend: null, touches: [], ...over,
  };
}
```

Create `dashboard/test/agentsData.test.ts`:

```ts
import { branchRows, stageReached, stateLabel, ageLabel, taskLabel, aheadBehindLabel, changesLabel, spendLabel } from "../src/agents/agentsData";
import { wt, session } from "./agentsFixtures";

test("branch rows: base first, live branches by fork point, the rest newest commit first", () => {
  const rows = branchRows({
    errors: [],
    worktrees: [
      wt({ path: "/repo", branch: "main", is_base: true, stage: null }),
      wt({ path: "/late", branch: "late", fork_time: 300, session_ids: ["a"] }),
      wt({ path: "/early", branch: "early", fork_time: 100, session_ids: ["b"] }),
      wt({ path: "/gone", branch: "gone", session_ids: ["c"], tip_time: 10 }),
      wt({ path: "/fresh", branch: "fresh", tip_time: 50 }),
    ],
  }, [session({ id: "a" }), session({ id: "b", state: "waiting" }), session({ id: "c", state: "ended" })]);
  expect(rows.base?.worktree.branch).toBe("main");
  expect(rows.active.map((r) => r.worktree.branch)).toEqual(["early", "late"]);
  expect(rows.active[0]!.session?.id).toBe("b");
  expect(rows.idle.map((r) => r.worktree.branch)).toEqual(["fresh", "gone"]);
});

test("the newest live session wins a worktree with two", () => {
  const rows = branchRows({ errors: [], worktrees: [wt({ path: "/x", session_ids: ["old", "new"] })] }, [
    session({ id: "old", last_event_at: "2026-10-10T10:00:00.000Z" }),
    session({ id: "new", last_event_at: "2026-10-10T11:00:00.000Z" }),
  ]);
  expect(rows.active[0]!.session?.id).toBe("new");
});

test("labels", () => {
  expect(stateLabel("waiting")).toBe("waiting on you");
  expect(stateLabel("quiet")).toBe("quiet");
  expect([stageReached(null), stageReached("worktree"), stageReached("review"), stageReached("merged")]).toEqual([0, 1, 4, 5]);
  expect(taskLabel(wt({ node_id: 12, node_title: "Detect pitch" }))).toBe("#12 Detect pitch");
  expect(taskLabel(wt({}))).toBe("off-plan");
  expect(aheadBehindLabel(wt({ ahead: 2, behind: 1 }))).toBe("↑2 ↓1");
  expect(aheadBehindLabel(wt({ ahead: null, behind: null }))).toBe("–");
  expect(changesLabel(wt({ changed_paths: ["a"] }))).toBe("1 file");
  expect(changesLabel(wt({ changed_paths: ["a", "b"] }))).toBe("2 files");
  expect(spendLabel({ in_tokens: 1000, out_tokens: 200, cost: 0.123 })).toBe("1200 tokens · $0.12");
  const now = Date.parse("2026-10-10T12:00:00Z");
  expect(ageLabel("2026-10-10T11:59:30.000Z", now)).toBe("just now");
  expect(ageLabel("2026-10-10T11:45:00.000Z", now)).toBe("15m ago");
  expect(ageLabel(now / 1000 - 3 * 3600, now)).toBe("3h ago");
  expect(ageLabel(now / 1000 - 2 * 86400, now)).toBe("2d ago");
});
```

Create `dashboard/test/AgentsTab.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AgentsTab } from "../src/agents/AgentsTab";
import * as client from "../src/api/client";
import { authed } from "../src/state";
import { wt, session } from "./agentsFixtures";

const SESSIONS = [
  session({ id: "run-1", origin: "launched", node_id: 12, node_title: "Detect pitch", cwd: "/w/node-12", last_action: "Edit core/pitch.py", spend: { in_tokens: 1000, out_tokens: 200, cost: 0.12 }, touches: ["core/pitch.py"] }),
  session({ id: "s-hand", cwd: "/repo/.worktrees/try", state: "waiting", last_action: "turn ended" }),
];
const WORKTREES = {
  errors: [],
  worktrees: [
    wt({ path: "/repo", branch: "main", is_base: true, stage: null }),
    wt({ path: "/w/node-12", branch: "node-12", ahead: 2, changed_paths: ["core/pitch.py"], fork_time: 100, tip_time: 200, stage: "commits", node_id: 12, node_title: "Detect pitch", node_status: "in_progress", session_ids: ["run-1"], planned: [{ glob: "core/*.py", touched: true }, { glob: "web/*.ts", touched: false }] }),
    wt({ path: "/repo/.worktrees/try", branch: "try", ahead: 1, changed_paths: ["a.md"], fork_time: 50, tip_time: 60, stage: "commits", session_ids: ["s-hand"] }),
    wt({ path: "/w/old", branch: "old", behind: 3, fork_time: 10, tip_time: Date.now() / 1000 - 7200, stage: "merged", merged: true }),
  ],
};

beforeEach(() => {
  authed.value = true;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/sessions")) return Promise.resolve({ sessions: SESSIONS });
    if (path === "/worktrees") return Promise.resolve(WORKTREES);
    return Promise.reject(new Error("unexpected " + path));
  });
});
afterEach(() => { vi.restoreAllMocks(); });

const branchOrder = () => [...document.querySelectorAll("[data-branch]")].map((e) => e.getAttribute("data-branch"));

test("branches show base first, then by fork point, with state, task and stage", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  expect(branchOrder()).toEqual(["main", "try", "node-12", "old"]);
  expect(screen.getByText("waiting on you")).toBeTruthy();
  expect(screen.getByText("off-plan")).toBeTruthy();
  expect(screen.getByText("#12 Detect pitch")).toBeTruthy();
  expect(screen.getAllByLabelText("stage: commits")).toHaveLength(2);
});

test("a worktree with no agent shows whether it is merged and its last commit", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("Worktrees with no agent"));
  expect(screen.getByText("merged · last commit 2h ago")).toBeTruthy();
});

test("a hand session's panel has no Stop", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("try"));
  fireEvent.click(document.querySelector('[data-branch="try"]')!);
  expect(screen.getByText(/started by hand/)).toBeTruthy();
  expect(screen.getByText("Off-plan: no task, so no planned files.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop agent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open diff" })).toBeNull();
});

test("a launched session's panel lists planned files and stops after a confirm", async () => {
  const post = vi.spyOn(client, "post").mockResolvedValue({ session_id: "run-1", signalled: 42 });
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  fireEvent.click(document.querySelector('[data-branch="node-12"]')!);
  expect(screen.getByText("core/*.py").nextSibling?.textContent).toBe("changed");
  expect(screen.getByText("web/*.ts").nextSibling?.textContent).toBe("not yet");
  fireEvent.click(screen.getByRole("button", { name: "Open session" }));
  expect(screen.getByText("1200 tokens · $0.12")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  expect(screen.getByText("Stop this agent?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/sessions/run-1/stop"));
});

test("signed out, the tab asks for sign-in and fetches nothing", () => {
  authed.value = false;
  render(<AgentsTab projectId={1} />);
  expect(screen.getByText("Sign in to see agent sessions and worktrees.")).toBeTruthy();
  expect(client.api).not.toHaveBeenCalled();
});
```

Append to `dashboard/test/touch-targets.test.ts`:

```ts
test(".branch-row meets the 44px touch-target minimum", () => {
  const css = readFileSync(join(__dirname, "..", "src", "agents", "agents.css"), "utf8");
  expect(minHeightOf(css, ".branch-row")).toBeGreaterThanOrEqual(44);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dashboard && npx vitest run test/agentsData.test.ts test/AgentsTab.test.tsx test/touch-targets.test.ts`
Expected: FAIL: `Failed to resolve import "../src/agents/agentsData"` and `ENOENT ... agents.css`.

- [ ] **Step 3: Create `dashboard/src/agents/agentsData.ts`**

```ts
// Shapes of GET /sessions and GET /worktrees, and the pure functions the
// Agents tab draws from them.

export type SessionState = "working" | "waiting" | "quiet" | "idle" | "ended";
export type Spend = { in_tokens: number; out_tokens: number; cost: number };
export type Session = {
  id: string; agent: string; origin: "launched" | "hand";
  node_id: number | null; node_title: string | null; node_status: string | null;
  cwd: string; worktree: string | null; branch: string | null; transcript_path: string | null;
  started_at: string; last_event_at: string; last_action: string | null; ended_at: string | null;
  waiting: boolean; state: SessionState; spend: Spend | null; touches: string[];
};
export type Stage = "worktree" | "commits" | "checks" | "review" | "merged";
export const STAGES: Stage[] = ["worktree", "commits", "checks", "review", "merged"];
export type Worktree = {
  path: string; branch: string | null; head: string | null; base: string | null; is_base: boolean;
  ahead: number | null; behind: number | null; changed_paths: string[];
  fork_time: number | null; tip_time: number | null; merged: boolean; error: string | null;
  session_ids: string[]; node_id: number | null; node_title: string | null; node_status: string | null;
  stage: Stage | null; planned: { glob: string; touched: boolean }[];
};
export type WorktreeList = { worktrees: Worktree[]; errors: string[] };
export type BranchRow = { worktree: Worktree; session: Session | null };

export function isLive(s: Session): boolean { return s.state !== "ended"; }

// Reuses the status pill colours, which the contrast test already covers.
export const STATE_CLASS: Record<SessionState, string> = {
  working: "st-in_progress", waiting: "st-awaiting_approval", quiet: "st-ready", idle: "", ended: "",
};

export function stateLabel(s: SessionState): string { return s === "waiting" ? "waiting on you" : s; }

function liveSession(ids: string[], byId: Map<string, Session>): Session | null {
  const live = ids.map((id) => byId.get(id)).filter((s): s is Session => s !== undefined && isLive(s));
  live.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
  return live[0] ?? null;
}

// The base branch first; then each branch with a live session, ordered by
// fork point (oldest first) so the lines drawn off the base never cross;
// the rest are "Worktrees with no agent", newest commit first.
export function branchRows(list: WorktreeList, sessions: Session[]): { base: BranchRow | null; active: BranchRow[]; idle: BranchRow[] } {
  const byId = new Map(sessions.map((s) => [s.id, s] as const));
  const rows = list.worktrees.map((w) => ({ worktree: w, session: liveSession(w.session_ids, byId) }));
  const others = rows.filter((r) => !r.worktree.is_base);
  const forkOf = (r: BranchRow) => r.worktree.fork_time ?? Number.MAX_SAFE_INTEGER;
  return {
    base: rows.find((r) => r.worktree.is_base) ?? null,
    active: others.filter((r) => r.session !== null).sort((a, b) => forkOf(a) - forkOf(b) || a.worktree.path.localeCompare(b.worktree.path)),
    idle: others.filter((r) => r.session === null).sort((a, b) => (b.worktree.tip_time ?? 0) - (a.worktree.tip_time ?? 0)),
  };
}

export function stageReached(stage: Stage | null): number { return stage === null ? 0 : STAGES.indexOf(stage) + 1; }

export function taskLabel(w: Worktree): string {
  return w.node_id !== null ? `#${w.node_id} ${w.node_title ?? ""}`.trim() : "off-plan";
}

export function changesLabel(w: Worktree): string {
  const n = w.changed_paths.length;
  return n === 1 ? "1 file" : `${n} files`;
}

export function aheadBehindLabel(w: Worktree): string {
  return w.ahead === null || w.behind === null ? "–" : `↑${w.ahead} ↓${w.behind}`;
}

export function spendLabel(s: Spend): string {
  return `${s.in_tokens + s.out_tokens} tokens · $${s.cost.toFixed(2)}`;
}

// `at` is an ISO timestamp (sessions) or unix seconds (commit times).
export function ageLabel(at: string | number, now: number): string {
  const ms = typeof at === "number" ? at * 1000 : Date.parse(at);
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
```

- [ ] **Step 4: Create `dashboard/src/agents/BranchPanel.tsx`**

```tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { openNode } from "../router";
import { refresh, toast } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { useAction } from "../ui/useAction";
import { STATE_CLASS, spendLabel, stateLabel, type BranchRow } from "./agentsData";

export function BranchPanel({ row, onClose }: { row: BranchRow; onClose: () => void }) {
  const w = row.worktree;
  const s = row.session;
  const stopA = useAction();
  const [details, setDetails] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  async function stop() {
    if (!s) return;
    setConfirmStop(false);
    const ok = await stopA.run(() => post(routes.sessionStop(s.id)));
    if (ok) { toast("stop sent to the agent"); refresh(); }
  }

  if (confirmStop) {
    return (
      <Confirm
        title="Stop this agent?"
        body="muvue sends SIGTERM to the agent's process group. The run records this attempt as failed."
        confirmLabel="Stop agent"
        danger
        onConfirm={stop}
        onCancel={() => setConfirmStop(false)}
      />
    );
  }

  return (
    <Sheet title={w.branch ?? w.path} onClose={onClose}>
      <div class="stack">
        <section class="stack tight">
          <h3>Session</h3>
          {s ? (
            <p class="row">
              <span class={"pill " + STATE_CLASS[s.state]}>{stateLabel(s.state)}</span>
              <span>{s.agent} · {s.origin === "launched" ? "started by muvue" : "started by hand"} · {s.last_action ?? "no action yet"}</span>
            </p>
          ) : <p class="muted">No agent is working here.</p>}
          {s && details ? (
            <dl class="kv">
              <dt>id</dt><dd>{s.id}</dd>
              <dt>directory</dt><dd>{s.cwd}</dd>
              <dt>transcript</dt><dd>{s.transcript_path ?? "—"}</dd>
              <dt>started</dt><dd>{s.started_at}</dd>
              <dt>last event</dt><dd>{s.last_event_at}</dd>
              <dt>files touched</dt><dd>{s.touches.join(", ") || "—"}</dd>
              {s.spend ? <><dt>spend</dt><dd>{spendLabel(s.spend)}</dd></> : null}
            </dl>
          ) : null}
        </section>
        <section class="stack tight">
          <h3>Planned files</h3>
          {w.node_id === null ? <p class="muted">Off-plan: no task, so no planned files.</p> : w.planned.length === 0 ? (
            <p class="muted">The task names no files.</p>
          ) : (
            <ul class="planned">
              {w.planned.map((p) => <li><span class="mono">{p.glob}</span><span class="caption planned-state">{p.touched ? "changed" : "not yet"}</span></li>)}
            </ul>
          )}
          {w.changed_paths.length ? (
            <>
              <h3>Changed files</h3>
              <ul class="planned">{w.changed_paths.map((p) => <li class="mono">{p}</li>)}</ul>
            </>
          ) : null}
          {w.error ? <div class="callout danger">{w.error}</div> : null}
        </section>
        <section class="stack tight">
          <h3>Overlap</h3>
          <p class="muted">No overlap checks yet.</p>
        </section>
        <div class="row">
          {s ? <Button onClick={() => setDetails((d) => !d)}>{details ? "Hide session" : "Open session"}</Button> : null}
          {w.node_id !== null ? <Button onClick={() => { onClose(); openNode(w.node_id!); }}>Open diff</Button> : null}
          {s && s.origin === "launched" ? (
            <Button variant="danger" busy={stopA.busy} busyLabel="Stopping…" onClick={() => setConfirmStop(true)}>Stop agent</Button>
          ) : null}
        </div>
        {stopA.error ? <div class="callout danger">{stopA.error}</div> : null}
      </div>
    </Sheet>
  );
}
```

- [ ] **Step 5: Create `dashboard/src/agents/AgentsTab.tsx`**

```tsx
import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { useApi } from "../hooks";
import { authed, refreshTick } from "../state";
import { Empty } from "../ui/Empty";
import {
  STAGES, STATE_CLASS, ageLabel, aheadBehindLabel, branchRows, changesLabel, stageReached, stateLabel, taskLabel,
  type BranchRow, type Session, type Stage, type WorktreeList,
} from "./agentsData";
import { BranchPanel } from "./BranchPanel";

// Liveness is computed by the server when asked; ask again this often so
// "working" turns "quiet" without a database change to trigger a refresh.
const RELOAD_MS = 30000;

function GraphCell({ base, first, last }: { base: boolean; first: boolean; last: boolean }) {
  // One trunk for the base branch down the left; each branch leaves it on
  // its own row, so rows ordered by fork point never cross.
  return (
    <svg class="git-graph" viewBox="0 0 28 52" preserveAspectRatio="none" aria-hidden="true">
      <line class="trunk" x1="8" y1={first ? 26 : 0} x2="8" y2={last ? 26 : 52} vector-effect="non-scaling-stroke" />
      {base ? <circle class="tip base" cx="8" cy="26" r="5" /> : (
        <>
          <path class="fork" d="M8 12 Q8 26 20 26" vector-effect="non-scaling-stroke" />
          <circle class="tip" cx="20" cy="26" r="4" />
        </>
      )}
    </svg>
  );
}

function StageBar({ stage }: { stage: Stage | null }) {
  const reached = stageReached(stage);
  return (
    <span class="stage-bar" role="img" aria-label={stage ? `stage: ${stage}` : "stage unknown"}>
      {STAGES.map((st, i) => <span class={"stage-seg" + (i < reached ? " on" : "")} title={st} />)}
    </span>
  );
}

function BranchLine({ row, first, last, onOpen }: { row: BranchRow; first: boolean; last: boolean; onOpen: () => void }) {
  const w = row.worktree;
  const s = row.session;
  return (
    <button type="button" class="list-row branch-row" data-branch={w.branch ?? ""} onClick={onOpen}>
      <GraphCell base={w.is_base} first={first} last={last} />
      <span class="branch-main">
        <span class="title mono">{w.branch ?? "(detached)"}</span>
        <span class="caption branch-sub">{w.path}</span>
      </span>
      <span class="branch-agent">
        {s ? (
          <>
            <span class={"pill " + STATE_CLASS[s.state]}>{stateLabel(s.state)}</span>
            <span class="caption"> {s.agent}{s.origin === "hand" ? " · by hand" : ""}</span>
          </>
        ) : <span class="caption">no agent</span>}
      </span>
      <span class="branch-task caption">{w.is_base ? "base branch" : taskLabel(w)}</span>
      {w.is_base ? null : (
        <>
          <span class="branch-changes caption">{w.error ?? `${changesLabel(w)} · ${aheadBehindLabel(w)}`}</span>
          <StageBar stage={w.stage} />
        </>
      )}
    </button>
  );
}

export function AgentsTab({ projectId }: { projectId: number | null }) {
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), RELOAD_MS);
    return () => clearInterval(t);
  }, []);
  const signedIn = authed.value;
  const sessionsQ = useApi<{ sessions: Session[] }>(
    () => (signedIn ? api(routes.sessions(projectId), { background: true }) : Promise.resolve({ sessions: [] })),
    [signedIn, projectId, refreshTick.value, tick],
  );
  const worktreesQ = useApi<WorktreeList>(
    () => (signedIn ? api(routes.worktrees(), { background: true }) : Promise.resolve({ worktrees: [], errors: [] })),
    [signedIn, refreshTick.value, tick],
  );
  const rows = useMemo(
    () => (worktreesQ.data && sessionsQ.data ? branchRows(worktreesQ.data, sessionsQ.data.sessions) : null),
    [worktreesQ.data, sessionsQ.data],
  );

  if (!signedIn) return <div class="agents-tab"><p class="muted">Sign in to see agent sessions and worktrees.</p></div>;
  const error = sessionsQ.error ?? worktreesQ.error;
  if (error) return <div class="agents-tab"><div class="callout danger">{error}</div></div>;
  if (!rows || !worktreesQ.data) return <div class="agents-tab"><Empty text="loading…" /></div>;

  const now = Date.now();
  const branches = [...(rows.base ? [rows.base] : []), ...rows.active];
  const open = [...branches, ...rows.idle].find((r) => r.worktree.path === selected) ?? null;
  return (
    <div class="agents-tab">
      {worktreesQ.data.errors.map((e) => <div class="callout danger">{e}</div>)}
      <section class="stack tight">
        <h2>Branches</h2>
        <div class="list">
          {branches.map((r, i) => (
            <BranchLine row={r} first={i === 0} last={i === branches.length - 1} onOpen={() => setSelected(r.worktree.path)} />
          ))}
        </div>
      </section>
      <section class="stack tight">
        <h2>Worktrees with no agent</h2>
        {rows.idle.length === 0 ? <p class="muted">None.</p> : (
          <div class="list">
            {rows.idle.map((r) => (
              <button type="button" class="list-row branch-row idle-row" data-branch={r.worktree.branch ?? ""} onClick={() => setSelected(r.worktree.path)}>
                <span class="branch-main">
                  <span class="title mono">{r.worktree.branch ?? "(detached)"}</span>
                  <span class="caption branch-sub">{r.worktree.path}</span>
                </span>
                <span class="caption branch-agent">
                  {r.worktree.error ?? `${r.worktree.merged ? "merged" : "not merged"} · ${r.worktree.tip_time !== null ? `last commit ${ageLabel(r.worktree.tip_time, now)}` : "no commits"}`}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
      {open ? <BranchPanel row={open} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
```

- [ ] **Step 6: Create `dashboard/src/agents/agents.css`**

```css
.agents-tab { padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 20px; min-width: 0; }
.branch-row { min-height: 52px; display: grid; grid-template-columns: 28px minmax(0, 1fr); grid-template-areas: "graph main" "graph agent" "graph task" "graph changes" "graph stage"; gap: 2px 10px; align-items: center; padding: 6px 16px 6px 8px; }
.branch-row.idle-row { grid-template-columns: minmax(0, 1fr); grid-template-areas: "main" "agent"; padding-left: 16px; }
.git-graph { grid-area: graph; align-self: stretch; width: 28px; height: 100%; min-height: 40px; }
.git-graph .trunk, .git-graph .fork { stroke: var(--text-2); stroke-width: 2; fill: none; }
.git-graph .tip { fill: var(--accent-strong); }
.git-graph .tip.base { fill: var(--text-2); }
.branch-main { grid-area: main; min-width: 0; display: flex; flex-direction: column; }
.branch-sub { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-agent { grid-area: agent; min-width: 0; }
.branch-task { grid-area: task; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-changes { grid-area: changes; min-width: 0; }
.stage-bar { grid-area: stage; display: inline-flex; gap: 3px; }
.stage-seg { width: 18px; height: 8px; border-radius: 2px; background: var(--surface-2); border: 1px solid var(--border); }
.stage-seg.on { background: var(--st-done); border-color: var(--st-done); }
.planned { margin: 0; padding-left: 18px; font-size: 13px; }
.planned-state { margin-left: 8px; }
@media (min-width: 900px) {
  .branch-row { grid-template-columns: 28px minmax(160px, 1.4fr) minmax(150px, 1fr) minmax(120px, 1fr) minmax(110px, auto) auto; grid-template-areas: "graph main agent task changes stage"; gap: 0 12px; }
  .branch-row.idle-row { grid-template-columns: minmax(160px, 1.4fr) minmax(150px, 1fr); grid-template-areas: "main agent"; }
}
```

- [ ] **Step 7: Wire the stylesheet and the tab**

In `dashboard/src/main.tsx`, add after `import "./cards/cards.css";`:

```tsx
import "./agents/agents.css";
```

In `dashboard/src/app.tsx`, add the import

```tsx
import { AgentsTab } from "./agents/AgentsTab";
```

and replace `<TabPlaceholder text="Agents view is loading." />` with:

```tsx
            <AgentsTab projectId={projectId.value} />
```

- [ ] **Step 8: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test`
Expected: typecheck exits 0; every vitest file passes, including `agentsData.test.ts`, `AgentsTab.test.tsx`, `touch-targets.test.ts`, `type-scale.test.ts`, `contrast.test.ts`, `no-stray-fetch.test.ts`.

- [ ] **Step 9: Build and run the static checks**

Run: `cd dashboard && npm run build && cd .. && uv run pytest tests/test_dashboard_static.py -q`
Expected: build succeeds; static tests PASS.

- [ ] **Step 10: Commit**

```bash
git add dashboard/src/agents dashboard/src/main.tsx dashboard/src/app.tsx dashboard/test/agentsFixtures.ts dashboard/test/agentsData.test.ts dashboard/test/AgentsTab.test.tsx dashboard/test/touch-targets.test.ts src/muvue/api/static/index.html
git commit -F - <<'MSG'
Show branches, agent sessions and worktrees in the Agents tab

A git graph down the left, one row per branch ordered by fork point,
with the agent's state, its task or off-plan, changes, ahead/behind and a
five-step stage bar; worktrees with no agent below; a side panel with
the session, planned files and Stop for sessions muvue launched.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

### Task 12: Protocol version 4, docs, UI check and end-to-end verification

**Files:**
- Modify: `src/muvue/core/config.py:131-138` (`PROTOCOL_VERSION = 4`), `:177` (default TOML)
- Modify: `tests/test_protocol_version.py`
- Modify: `dashboard/scripts/ui-check.mjs` (Agents tab at 390 and 1280 wide)
- Modify: `docs/protocol.md`, `docs/decisions.md` (append #177), `README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `muvue.core.config.PROTOCOL_VERSION == 4`; documentation of every new event, tool, route and config key.

- [ ] **Step 1: Write the failing test**

Replace `tests/test_protocol_version.py` with:

```python
"""protocol_version 4: mission control phase 1 -- the UserPromptSubmit and
SessionEnd hook events and session fields on hook queue lines, the MCP
session tools, and GET /sessions, GET /worktrees, POST /sessions/{id}/stop
(3 was the project canvas backend; 2 the W4-W11 verbs and flags, v4
section 4, "versioned via protocol_version"). A repo whose config.toml
says an older version gets a doctor warning, and so do its adapters."""

from __future__ import annotations

import tomllib

from muvue.core import config as config_mod, doctor
from muvue.core.repo_init import init_repo


def test_new_repos_speak_the_current_protocol(tmp_path):
    assert config_mod.PROTOCOL_VERSION == 4
    assert tomllib.loads(config_mod.DEFAULT_CONFIG_TOML)["protocol_version"] == 4
    assert config_mod.MuvueConfig().protocol_version == 4
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not [w for w in report.warnings if "protocol_version" in w]


def test_doctor_warns_on_a_config_from_an_older_protocol(tmp_path):
    init_repo(tmp_path)
    path = tmp_path / ".muvue" / "config.toml"
    path.write_text(path.read_text().replace("protocol_version = 4", "protocol_version = 3"))
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert any("protocol_version=3" in w and "protocol_version=4" in w for w in report.warnings)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_protocol_version.py -q`
Expected: FAIL with `assert 3 == 4`.

- [ ] **Step 3: Bump the protocol**

In `src/muvue/core/config.py`, extend the comment above `PROTOCOL_VERSION` with:

```python
# 4: mission control phase 1 -- UserPromptSubmit/SessionEnd hooks and
# session fields on hook queue lines, the MCP session_start/
# session_heartbeat/session_end tools, GET /sessions, GET /worktrees and
# POST /sessions/{id}/stop.
```

and change `PROTOCOL_VERSION = 3` to `PROTOCOL_VERSION = 4`. In `DEFAULT_CONFIG_TOML`, change `protocol_version = 3` to `protocol_version = 4`.

- [ ] **Step 4: Run the protocol and doctor tests**

Run: `uv run pytest tests/test_protocol_version.py tests/test_doctor_drivers.py tests/test_adapters.py tests/test_adapter_user_scope.py tests/test_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 5: Extend `docs/protocol.md`**

Change the first line's version to `` `protocol_version = 4` `` and add, after the "Version 3 (the project canvas backend) changed:" list:

```markdown
Version 4 (mission control phase 1) changed:
- The Claude Code adapter installs `UserPromptSubmit` and `SessionEnd`
  hooks too, and every hook queue line of a session carries
  `session_id`, `cwd`, `transcript_path`, `agent` and, for an agent the
  runner launched, `launched_session`.
- `muvue adapter install claude-code --user` installs record-only hooks
  in `~/.claude/settings.json`.
- New MCP tools `session_start`, `session_heartbeat`, `session_end`.
- New endpoints `GET /sessions`, `GET /worktrees`,
  `POST /sessions/{id}/stop`.
- `sessions` and `session_touches` tables (`SCHEMA_VERSION` 8 -> 9).
```

In the "Claude Code / Codex / Gemini / Cursor adapters (P3)" section, change the list of events in the `claude-code` bullet from `` `SessionStart`, `PreToolUse`, `PreCompact`, `Stop` `` to `` `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PreCompact`, `Stop`, `SessionEnd` ``.

Append a new section before "## Leases and optimistic version (P3, ships v0.1)":

```markdown
## Sessions and worktrees (protocol_version 4, mission control phase 1)

A **session** is one run of an agent CLI. `launched` sessions are the
runner's (`run-<uuid>`); every other session is `hand`. Rows live in
`sessions`; repository-relative paths a session edited live in
`session_touches`. Decision #177 explains the design.

**Hook queue lines.** Every Claude Code hook line of a session adds
`session_id`, `cwd`, `transcript_path` (when the payload has them),
`agent: "claude"`, and `launched_session` when `MUVUE_SESSION` is set
(the runner sets it for the agents it launches). A payload without a
`session_id` spools exactly what it did before. Shapes:

- `{"event": "pre-tool-use", "ts", "node_id", "tool_name", ["file_path"], <session>}`:
  one line per Edit/Write/Bash call in a session (`file_path` for Edit
  and Write). No DB open, no subprocess.
- `{"event": "user-prompt-submit", "ts", "node_id", <session>}`: the
  prompt text is never spooled.
- `{"event": "session-end", "ts", "node_id", ["reason"], <session>}`.
- `session-start`, `stop`, `pre-compact` lines as before, plus `<session>`.
- `{"event": "hook_error", "ts", "hook", "error"}`: a Claude Code hook
  raised; it exits 0 and the agent continues.

A Claude Code hook whose directory has no `.muvue/` above it reads the
first `.git` *file* above it (`gitdir: <common>/worktrees/<name>`) and
uses `<common>`'s repository: `<common>/..` for a worktree of the
checkout, or the repository named by the airlock's `muvue-repo-root`
marker for a strict-mode worktree. Such a hook only records; it does not
allow or deny, since `.muvue/current_node` belongs to the main
checkout. Git hooks do not do this walk.

**User-level install.** `muvue adapter install claude-code --user`
writes the six hooks to `~/.claude/settings.json` with a
`MUVUE_HOOK_SCOPE=user` prefix. Those hooks only record, never block,
exit 0 at once outside a registered repository, and record nothing when
the project's own `.claude/settings.json` already runs muvue's hooks.
`doctor` prints `claude code hooks: repo`, `user`, `repo, user` or `not
installed`, and warns when the repository's own install lacks an event.

**Drain.** `drain_queue` folds each session line into `sessions` in the
transaction that writes its `hook.<event>` audit event. `pre-tool-use`
and MCP `heartbeat` lines update the session without an audit event. A
line can create only a `hand` row and never sets `pid`. A session's node
is the node whose `worktree` contains `cwd`; else, for a launched agent,
the runner's node (the agent's own id is stored as `alias_of` the run);
else an MCP `bind_node_id`; else none (off-plan). `last_action` is
`session started`, `prompt sent`, `<Tool> <path>`, `turn ended`,
`compacting`, `session ended`, `run ended`, or a heartbeat's `action`.
`waiting` is 1 after `stop` and 0 after any other line.

**Liveness**, computed on read from `[sessions]` (`working_s = 120`,
`idle_s = 900`, `ended_s = 7200`): `ended` (ended_at set, or no event
for more than `ended_s`), then `waiting` (after Stop), then `working`
(event within `working_s`), then `idle` (no event for more than
`idle_s`), else `quiet`.

**MCP tools.** `session_start(agent, cwd, node_id?)` returns
`{"session_id": "mcp-<uuid>"}`; `session_heartbeat(session_id, action?,
touched_paths?)`; `session_end(session_id)`. They spool `session-start`,
`heartbeat` and `session-end` lines. Bad arguments are `-32000` errors
naming the argument.

**Runner.** `run_node` inserts the `launched` row before the driver
starts, passes `MUVUE_SESSION=<run id>` in the driver's environment,
records the driver's pid (its process group), drains the agent's lines,
and then ends the row.

**Endpoints** (session-gated; the POST is JSON-only):

- `GET /sessions?project_id=N&include_ended=0`: `{"sessions": [...]}`,
  newest event first. Each has the `sessions` columns plus `state`,
  `node_title`, `node_status`, `worktree`, `touches` and `spend`
  (`{"in_tokens", "out_tokens", "cost"}` summed from `node_usage` for a
  launched session's node and agent; null for hand sessions). With
  `project_id`, off-plan sessions are included.
- `GET /worktrees`: `{"worktrees": [...], "errors": [...]}` for the
  checkout and the strict-mode airlock. Each row: `path`, `branch`,
  `head`, `base`, `is_base`, `ahead`, `behind`, `changed_paths`,
  `fork_time`, `tip_time`, `merged`, `error`, `session_ids`, `node_id`,
  `node_title`, `node_status`, `stage` (`worktree`, `commits`, `checks`,
  `review`, `merged`) and `planned` (`[{"glob", "touched"}]`). A git
  failure is `git failed: <first line of stderr>` on the row, or in
  `errors` for a whole listing. Branch status is cached by sha.
- `POST /sessions/{id}/stop`: SIGTERM to a launched session's process
  group; 200 `{"session_id", "signalled"}`, records `session.stopped`.
  409 `started by hand; stop it where it runs` for a hand session; 409
  for an ended session or one with no process; 404 for an unknown id.
```

- [ ] **Step 6: Append decision #177 to `docs/decisions.md`**

```markdown
177. **Agent sessions and worktrees are recorded (mission control phase
    1).** muvue used to know only the nodes its runner drove; a person's
    own Claude Code session, or two sessions in one repository, were
    invisible. Now every session in a repository has a `sessions` row
    (schema 9), and the dashboard's Agents tab shows them against the
    repository's worktrees.
    - The hooks still never touch the DB to record: a session line is
      one `O_APPEND` write, folded in by the drain. The queue is
      untrusted: a line can create only a `hand` row and never sets
      `pid`, the one field Stop signals.
    - A session's node comes from its worktree, then from the run that
      launched it (`MUVUE_SESSION`; the agent's own id is an alias of
      the run), then from an MCP `node_id`, never from
      `.muvue/current_node`, which is one file for the whole repository.
    - Claude Code hooks in a worktree outside the repository find it
      through the worktree's `.git` file (or the strict-mode airlock's
      marker) and only record. Git hooks do not: a `post-commit` there
      would spool the main checkout's HEAD, and decision #153 already
      links worktree commits at `done`.
    - `adapter install claude-code --user` installs record-only hooks
      machine-wide. They never block an edit, because a repository that
      never installed the adapter must not start refusing edits, and
      they stand down when the project's own hooks are installed, so no
      event is counted twice.
    - Liveness is computed on read: working, waiting on you, quiet (no
      event for 2 to 15 minutes; possibly a long tool call), idle, ended.
      The spec named four states; "quiet" fills the gap between its
      working and idle rules.
    - PreToolUse and MCP heartbeat lines update the session without a
      `hook.*` audit event; one audit row per tool call would swamp
      `events`.
    - Stop exists only for launched sessions: SIGTERM to the process
      group the runner recorded. A hand session is refused with 409.
    - Sessions are observations, not decisions, so `rebuild` does not
      replay them.
    Cost if wrong: only Edit, Write and Bash calls refresh a session, so
    an agent that only reads for more than two minutes shows "quiet";
    and a branch created at an old base commit with nothing on it reads
    as merged.
```

- [ ] **Step 7: Update `README.md` and `CHANGELOG.md`**

In `README.md`, "Using it from your own agent session": add to the code block, after `muvue adapter install claude-code   # hooks in .claude/settings.json`:

```bash
muvue adapter install claude-code --user   # record-only hooks for every Claude Code session on this machine
```

and add after the paragraph that ends "on CI, p99 is 34 ms per call.":

```markdown
The hooks also record each Claude Code session: which worktree and
branch it is in, what it last did, which files it edited, and whether it
is working, waiting for you, quiet, idle or ended. `--user` installs the
hooks once for every Claude Code session on the machine, including
sessions in worktrees outside the repository; those hooks only record
and never block an edit. Other agent CLIs report through the MCP tools
`session_start`, `session_heartbeat` and `session_end`.
```

In "Dashboard", add after the bullet list that ends with "`⌘K` opens a jump-to palette.":

```markdown
A project has four tabs: **Map** (the plan diagram), **Plan** and
**Repo** (placeholders for now), and **Agents**: every branch with a git
graph down the left, the agent working on it and its state, its task or
"off-plan", changed files, commits ahead and behind, and a five-step
stage bar (worktree, commits, checks, review, merged); worktrees with no
agent below. Tap a branch for its session, its task's planned files and,
for agents muvue started, Stop.
```

In "Configuration", add a table row after `[budget]`:

```markdown
| `[sessions]` | seconds before a session counts as quiet (`working_s`), idle (`idle_s`) and ended (`ended_s`) |
```

In "Docs", change `(protocol version 2)` to `(protocol version 4)`.

In `CHANGELOG.md`, under `## [Unreleased]` / `### Added`, add:

```markdown
- Agent sessions and worktrees (mission control phase 1): the Claude Code hooks record every session (also `UserPromptSubmit` and `SessionEnd`), `muvue adapter install claude-code --user` records sessions machine-wide without blocking edits, other agents report through the MCP tools `session_start`/`session_heartbeat`/`session_end`, and the runner records the sessions it launches. `GET /sessions`, `GET /worktrees` and `POST /sessions/{id}/stop` (launched sessions only) serve the dashboard's new Agents tab. New `[sessions]` config table; schema version 9; protocol version 4 (set `protocol_version = 4` in `.muvue/config.toml` and re-run `muvue adapter install claude-code`). See decision #177.
```

- [ ] **Step 8: Add the Agents tab to the UI check**

In `dashboard/scripts/ui-check.mjs`, inside the `if (linked) {` block, after `await f.screenshot({ path: join(outDir, "07-desktop-signed-in.png") });` and before `await fresh.close();`, add:

```js
    // --- Agents tab (mission control phase 1) at phone and desktop width
    await f.goto(base + "#/p/" + projectId + "?tab=agents");
    for (const [w, h] of [[1280, 800], [390, 844]]) {
      await f.setViewportSize({ width: w, height: h });
      const shown = await f.waitForSelector(".agents-tab h2", { timeout: 5000 }).then(() => true, () => false);
      await f.screenshot({ path: join(outDir, `08-agents-${w}.png`), fullPage: true });
      check(`agents tab ${w}: the Branches section renders`, shown && (await f.getByRole("heading", { name: "Branches" }).count()) === 1);
      check(`agents tab ${w}: the base branch row is listed`, (await f.locator(".branch-row").count()) >= 1);
      check(`agents tab ${w}: no horizontal page scroll`, await f.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth));
      const short = await f.locator(".project-tabs button, .branch-row").evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().height < 44).length);
      check(`agents tab ${w}: every tab and branch row is at least 44px tall`, short === 0, `${short} too short`);
    }
```

- [ ] **Step 9: Run every gate**

Run, from the worktree root:

```bash
uv run pytest -q
uv run pytest -q --cov=muvue.core
cd dashboard && npm run typecheck && npm test && npm run build && cd ..
git status --porcelain src/muvue/api/static/index.html
uv run pytest tests/test_dashboard_static.py tests/test_hook_latency_benchmark.py -q
```

Expected: all tests pass; coverage report ends at or above 85% (`Required test coverage of 85% reached`); typecheck and vitest pass; the build leaves `index.html` unchanged since Task 11's commit (empty `git status` line), or, if it changed, it is committed in Step 11.

- [ ] **Step 10: Run the UI check**

Run:

```bash
PLAYWRIGHT_CORE=/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core \
CHROME_PATH=$HOME/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell \
node dashboard/scripts/ui-check.mjs /tmp/muvue-ui-check-p1
```

Expected: every line `PASS`, ending `N/N checks passed` with the eight new `agents tab` checks among them. If either path is missing on this machine, the script exits 2 with "set PLAYWRIGHT_CORE and CHROME_PATH"; then say plainly in the hand-off that the UI check was not run, and why.

- [ ] **Step 11: Live check against a real session**

Use the `projects/muvue:verify` skill's recipe to drive the CLI. In a scratch repository (never the user's repositories, and never restarting a daemon the user is running without asking):

```bash
tmp=$(mktemp -d) && cd "$tmp" && git init -q -b main && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
uv run --project /home/eugene/projects/muvue/.worktrees/mission-control-p1 muvue init .
uv run --project /home/eugene/projects/muvue/.worktrees/mission-control-p1 muvue adapter install claude-code
printf '%s' "{\"session_id\":\"live-1\",\"cwd\":\"$tmp\",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$tmp/a.py\"}}" \
  | uv run --project /home/eugene/projects/muvue/.worktrees/mission-control-p1 muvue hook pre-tool-use .; echo "exit $?"
uv run --project /home/eugene/projects/muvue/.worktrees/mission-control-p1 muvue status >/dev/null
sqlite3 .muvue/muvue.db "SELECT id, origin, last_action FROM sessions; SELECT session_id, path FROM session_touches;"
```

Expected: `exit 2` (an Edit with no active node is still blocked in the main checkout), then the sessions query prints `live-1|hand|Edit a.py` and the touches query `live-1|a.py` (the `status` command's CLI callback drained the queue). Report the actual output.

- [ ] **Step 12: Commit**

```bash
git add src/muvue/core/config.py tests/test_protocol_version.py dashboard/scripts/ui-check.mjs docs/protocol.md docs/decisions.md README.md CHANGELOG.md
git add src/muvue/api/static/index.html
git commit -F - <<'MSG'
Bump the protocol to 4 and document sessions, worktrees and the Agents tab

Decision #177, docs/protocol.md, README, CHANGELOG, and the Agents tab
in ui-check at phone and desktop widths.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
MSG
```

---

## Self-review

**Spec coverage (Phase 1).**

| Spec requirement | Task |
|---|---|
| Queue lines gain `session_id`, `cwd`, `transcript_path`, `agent` | 2 |
| `UserPromptSubmit`, `SessionEnd` installed by the adapter | 2 (hook), 3 (adapter) |
| PreToolUse appends one line per call, `file_path` for Edit/Write, no SQLite/subprocess | 2 |
| Node decided at drain: worktree, launched, none; `current_node` not used | 4 |
| `.git` file walk from a worktree | 2 (plus airlock, deviation 5) |
| `--user` install, exit 0 outside a registered repo, doctor reports which is active | 3 (record-only, deviation 3) |
| `sessions`, `session_touches`, schema 9 | 1 |
| Drain upserts in the same transaction as `hook.*` events | 4 (deviation 7 for PreToolUse/heartbeat) |
| Spend for launched from `node_usage`; none for hand | 5 |
| Liveness with `[sessions]` thresholds | 1 (config), 5 (deviation 6) |
| Runner inserts the launched row with pid, cwd, sets `ended_at`, `MUVUE_SESSION`, alias | 6 (deviation 11) |
| MCP `session_start`, `session_heartbeat`, `session_end` | 7 |
| `list_worktrees`, `branch_status`, cache per repository and HEAD sha | 8 (deviation 13) |
| Worktree with no live session: "no agent", last commit time, merged | 8 (data), 11 (view) |
| `GET /sessions`, `GET /worktrees`, `POST /sessions/{id}/stop` with 409 for hand | 9 |
| Stage `worktree`, `commits`, `checks`, `review`, `merged` | 8 (deviations 12, 14) |
| Tabs Map, Plan, Agents, Repo; Plan and Repo placeholders | 10 |
| Branches section with git graph, columns, stage bar; "Worktrees with no agent"; side panel with Open session / Open diff / Stop (launched only) | 11 (deviation 18) |
| Error handling: `hook_error`, drop lines with no repository, git failures on the row, refused stop | 2, 2, 8, 9 |
| Testing: golden lines, state transitions, real `git worktree` fixtures, `.git` file walk, `--user` round trip with `uninit`, vitest per view, guard tests, `ui-check` at 390 and 1280 | 2, 5, 8, 2, 3, 10-11, 10-11, 12 |
| Docs | 12 |

**Placeholder scan.** No step says "TBD", "add error handling" or "similar to Task N"; every code step shows the code. The one placeholder string in the product, `Agents view is loading.` in Task 10, is replaced by Task 11.

**Type consistency.** `apply_line(conn, event)`, `session_state(row, now, cfg)`, `list_sessions(conn, cfg, *, project_id, include_ended, now)`, `start_launched(conn, *, node_id, project_id, agent, cwd)`, `set_pid`, `end_launched`, `SessionError`, `spool_start/spool_heartbeat/spool_end`, `launched_run_finished`, `get_session`, `stop_launched(conn, session_id, cfg, *, actor_evidence, now, kill)`, `branch_status(repo_root, *, base_sha, tip_sha)`, `repo_worktrees(conn, repo_root, config, *, now)` are spelled the same in every task that defines or calls them. The JSON keys the dashboard types read (`state`, `session_ids`, `is_base`, `fork_time`, `tip_time`, `stage`, `planned`, `spend`) match the Python dicts in Tasks 5 and 8.
