# Changelog

All notable changes to this project are documented here.

## [Unreleased] - P3 (ships v0.1, light mode)

### Added
- **Leases enforced at `start`-time**: a second owner racing an
  `in_progress` node gets a precise "leased by X until Y" `NodeError`
  (`core.nodes.start`), on top of the state machine's existing implicit
  refusal. `planning.lease_minutes` config field (default 60) now drives
  CLI/API `start` instead of a hardcoded module constant.
- **Optimistic `nodes.version`**: `core.nodes.bump_version` (+
  `node.version_bumped` event) is called by `add_note(actor="human")`
  and `core.gates.edit_criteria`. `done`/`fail` take `expected_version`;
  a mismatch raises `VersionMismatch` (a `NodeError` subclass) instead of
  overwriting a human edit made mid-task. CLI `done`/`fail` gained
  `--version`.
- **`core.trailers.parse_node_ids`**: extracts every `Muvue-Node:`/
  `Refs:` trailer's node id from a commit message, de-duplicated across
  *all* matching lines -- survives a squash-merge message that
  concatenates several original commits' trailers.
- **`core.hooks.handle_post_commit`**: links a commit's resolvable node
  ids into `node_commits`, records `commit.linked`, and enqueues
  `anchor.hash_requested`/`staleness.flagged` no-op events (P2's
  documented no-op-consumer pattern; real anchor hashing/staleness is
  structure-layer, P6+). `muvue hook post-commit` now runs it for real.
- **`core.gates.HumanOnly` / `core.nodes.HumanOnly`**: `approve_spec`,
  `approve_node`, `approve_gate2`, `revisions.approve_revision`,
  `nodes.approve_review`, `nodes.reject_review` now refuse a non-`human`
  actor themselves, not only via the CLI/API/MCP surface never routing
  to them (plan section 4: human verbs "never exposed over MCP").
- **`tests/fake_agent.py`**: scripted cooperative/lazy/adversarial
  behaviours (plan section 10) exercised against `muvue.core` directly;
  every adversarial behaviour is asserted blocked/flagged by core, not
  by the script.
- **`src/muvue/mcp_server.py`**: hand-rolled JSON-RPC-over-stdio MCP
  server (`muvue mcp [PATH]`), no new dependency. Exposes exactly
  `brief/show/start/done/fail/note/ask/wait/replan/status` -- never the
  human verbs.
- **`core.queries`**: real `show_node`/`brief_node`/`status_summary`
  backing the CLI's previously-stubbed `brief`/`show`/`status` (and
  `note`, wired to the existing `add_note`) -- the MCP server needed
  real implementations for the full agent-verb set.
- **Claude Code adapter**: `muvue adapter install claude-code` writes/
  merges `.claude/settings.json`'s `hooks` (idempotent, preserves
  unrelated config, embeds `protocol_version`). `core.claude_hooks` +
  `muvue hook session-start|pre-tool-use|pre-compact|stop` implement
  SessionStart -> `brief`, PreToolUse -> block Edit/Write with no
  `in_progress`/an `awaiting_approval` node and block `git commit`
  without a trailer, PreCompact -> require a summary, Stop -> block
  ending the turn with unlogged `in_progress` work.
  `core.adapters.set_current_node`/`get_current_node` (`.muvue/
  current_node`, gitignored) bridge `start`/`done`/`fail` to the hooks.
- **Codex/Gemini/Cursor adapters**: `muvue adapter install
  codex|gemini|cursor` write best-effort, **unverified** instruction
  files (`AGENTS.md`/`GEMINI.md`/`.cursor/rules/muvue.mdc`) pointing at
  the CLI/MCP surface -- see `docs/providers.md`.
- **`core.doctor`**: warns when an installed adapter's embedded
  `protocol_version` no longer matches the repo's current one.
- **`core.review.dispatch`**: light-mode-only criteria-mode gate on top
  of P2's tier/flag gate -- `manual` always waits for a human, `external`
  is always flagged, `auto` runs an injectable `run_checks` (opt-in;
  `core.review.default_run_checks` is a real subprocess runner, not yet
  auto-wired into CLI/API -- see `docs/decisions.md`).

### Scope notes (see `docs/decisions.md`)
- Strict mode, the worktree-per-node airlock, the real runner/drivers
  (spawning vendor CLIs), the structure/drift layer, and the VS Code
  extension remain out of scope for P3 (ships P4+).
- `default_run_checks` exists and is tested but is not auto-invoked by
  CLI/API `done` yet (would run the project's real test command as a
  side effect of every `done` call) -- flagged as needing a decision
  before v0.1 truly ships.
- Codex/Gemini/Cursor adapter file formats are best-effort guesses, not
  verified against live vendor docs (no network access in this
  environment).

## [Unreleased] - P2

### Added
- `muvue.core.risk`: single source of truth for risk-tier computation
  (plan section 5) -- `compute_tier` (diff size via `predicted_touches`
  count against `planning.max_files_per_task`/`risk.max_diff_lines`, path
  globs via `risk.globs`, deletions via an explicit `has_deletions` flag
  with no producer yet, and `criteria_edited` forcing `high`
  unconditionally), `max_tier` (never-downgrade merge), `is_flagged`
  (test-file touches are always flagged). `core.gates.edit_criteria` and
  `core.gates.approve_node`'s initial-freeze tiering now both route
  through this module instead of special-casing `"high"`.
- `muvue.core.nodes.done(..., config=None)`: when `config` is passed
  (CLI and API always do), `done` is gated by risk tier instead of always
  completing unconditionally -- low tier and unflagged auto-approves
  straight through to `done`; anything else stops at `review`. With
  `config=None` (every P0/P1 call site), behavior is unchanged.
- `muvue.core.nodes.approve_review` / `reject_review`: human
  `review -> done` / `review -> in_progress` transitions. Logs
  `metric.rubber_stamp` when time-to-approve is under 10s.
- `muvue.core.daemon`: `reconcile_leases` / `process_queue` /
  `reconcile_on_start` (expired `in_progress` leases revert to `ready`
  with `attempts + 1`, or `failed` if that exhausts `max_attempts`; the
  event queue drains via `events.acked_at`, consumers are documented
  no-ops for P2), and repo-scoped session tokens (`create_session` /
  `verify_session`, `<repo>/.muvue/session`) gating human-verb API calls.
- `muvue.api`: FastAPI app (`create_app`) mirroring the CLI verbs 1:1,
  OpenAPI-documented, SSE at `GET /events/stream` (one long-lived
  connection per stream polling `PRAGMA data_version`), `GET
  /nodes/{id}/diff`, `GET /nodes/{id}/logs` (NDJSON event stream), `POST
  /nodes/{id}/start?agent=X` (agent recorded via a `node.agent_requested`
  event, no spawning -- drivers ship P5), `GET /inbox`, `GET /kpis`
  (`drift_pct`/`tokens_per_node`/`spend_vs_budget` stubbed at 0.0 pending
  the structure layer and `node_usage` population; `rubber_stamp_rate` is
  real), `GET /events` (timeline), `GET /projects/{id}/revisions`
  (plan-revision history), `POST /nodes/{id}/comment` (spec inline
  comments, reuses `feedback` notes). Human verbs (`approve`, `reject`,
  `ack` via `POST /events/{id}/ack`, `pause`, `resume`, `close`; `merge`,
  `handoff`, `import` stay stubs) require `Authorization: Bearer <session
  token>`.
- `muvue serve [PATH] [--host] [--port]`: one daemon per repo. Runs
  `core.daemon.reconcile_on_start` before opening the socket, mints and
  prints a fresh session token, then serves the FastAPI app via uvicorn.
- One embedded vanilla-JS dashboard (`src/muvue/api/static/index.html`,
  no CDN loads, no inline event handlers): tree/DAG-as-list view, node
  panel (criteria/notes/summary/commits, approve/reject/start-with-agent
  actions), spec view with inline comments, inbox, event timeline,
  plan-revision history, KPI tiles, pause/resume buttons.
- `nodes.start` now also refuses while `project.phase == "paused"` (plan
  section 5 "Emergency stop": pause refuses start).

### Fixed
- SSE polling `PRAGMA data_version` from a *new* connection opened on
  every poll does not reliably observe writes committed by other
  connections (verified independently of FastAPI/Starlette -- see
  `docs/decisions.md`); the stream now holds one connection for its whole
  lifetime, which is the documented, correct way to observe
  `data_version` changes.

### Scope notes (see `docs/decisions.md`)
- Drivers/runner, MCP server, Codex/Gemini/Cursor adapters, git hook
  business logic, trailers, structure-graph/drift logic, and strict mode
  remain out of scope for P2; they ship P3+.
- `merge`, `handoff`, `import` API endpoints are stubs (need P5 runner /
  P6 close-and-export / P6 GitHub import respectively).
- KPI fields `drift_pct`, `tokens_per_node`, `spend_vs_budget` are
  stubbed at `0.0`: they need the structure layer (P3+) and `node_usage`
  population (P5), neither of which exists yet.

## [Unreleased] - P1

### Added
- `muvue.core.gates`: Gate 1 (`submit_spec` / `approve_spec`: spec node
  `pending -> ready`), Gate 2 (`approve_gate2`: approves every pending
  task/subtask node under a project, freezes `criteria_json` into
  `criteria_hash`, and flips `project.phase` `planning -> executing`),
  `approve_node` (single-node freeze + approval, reused for Gate 2, re-
  approval after a criteria edit, and plan-revision approval), granularity
  lint (`lint_task`: warns, never blocks, on `predicted_touches` count over
  `planning.max_files_per_task`, subtask count over `planning.max_subtasks`,
  or `criteria_mode != "auto"`), and `edit_criteria` (re-tiers `risk_tier`
  to `high` and demotes `ready -> pending` when a frozen node's criteria
  hash changes).
- `nodes.start` now refuses `ready -> in_progress` while
  `project.phase == "planning"`, raising `NodeError`.
- `nodes.create_node` no longer sets `criteria_hash` at creation; it stays
  `NULL` until Gate 2 (or a revision re-approval) freezes it. This is the
  signal `edit_criteria` uses to detect a post-freeze edit.
- `nodes.to_pending` (`ready -> pending`, reuses the existing state-machine
  edge) for the criteria-edit re-tier path.
- `projects.set_phase`: records `project.phase_changed` so phase changes
  replay correctly through `rebuild`.
- `muvue.core.asks`: `ask`/`answer`/`wait`/`get_question` backing a new
  `questions` table. `ask` creates an open question with a proposed
  default; `wait` takes an injectable `now` so timeout behavior is
  testable without sleeping for real minutes. Past
  `planning.ask_timeout_minutes`: `--default-ok` applies the default as a
  `feedback` note and the node proceeds; otherwise the node moves to
  `blocked(question)`.
- `muvue.core.revisions`: `plan_revision_nodes` snapshot table,
  `propose_revision` (snapshots a node set's criteria hashes under a new
  revision `n`), `diff_revision` (added/removed/changed/unchanged vs.
  `n - 1`), `approve_revision` (diff-only: only `added`/`changed` nodes are
  re-approved via `approve_node`; `removed` nodes are soft-deleted;
  `unchanged` nodes are never read or written), and
  `replan_add_subtask` (adds a subtask under an already-Gate-2-approved
  task directly to `ready`, no new approval, since it inherits the
  parent's approved scope).
- CLI: `approve spec:ID|node:ID|gate2:PROJECT_ID|revision:PROJECT_ID:N`,
  `ask NODE_ID --question TEXT --default TEXT`, `wait QUESTION_ID
  [--timeout SECONDS] [--default-ok]` (polling wrapper over
  `core.asks.wait`), `replan PARENT_TASK_ID --title TEXT`,
  `propose-revision PROJECT_ID --node-ids ID,ID,...` are real now
  (previously stubs).
- `nodes.create_node(..., predicted_touches=[...])` to populate
  `predicted_touches` at creation, used by the granularity lint.

### Changed
- Bumped `protocol_version`-relevant verb behavior for `approve`, `ask`,
  `wait`, `replan` (see `docs/protocol.md`); config's
  `protocol_version` value itself is unchanged in P1 (no wire-format
  break, only new verb bodies).

### Scope notes (see `docs/decisions.md`)
- Daemon/dashboard, drivers, structure-graph logic, leases beyond P0's
  lease_until expiry check, `reject`/`ack`/`merge`/`close`/`pause`/
  `resume`/`handoff`/`import` remain stubs; they ship P2+.

## [Unreleased] - P0

### Added
- `muvue.core`: single write path for all state mutations (`db`, `events`,
  `state_machine`, `projects`, `nodes`, `config`, `repo_init`, `doctor`,
  `migrate`, `rebuild`).
- SQLite schema (WAL, `busy_timeout=5000`, FTS5 over `notes`) covering the
  full Process graph (`projects`, `plan_revisions`, `nodes`, `deps`,
  `predicted_touches`, `notes`, `events`, `node_commits`, `node_usage`,
  `external_refs`) and an empty Structure-graph schema (`components`,
  `component_edges`, `decisions`, `invariants`, `node_touches`,
  `project_links`) for future phases.
- Node status machine (`pending -> ready -> in_progress -> review -> done`,
  side states `awaiting_approval`, `blocked(reason)`, `failed`) with
  exhaustive transition table and lease-owner enforcement.
- Pydantic v2 config model for `.muvue/config.toml`, strict validation
  (`extra="forbid"`, literal enums, cross-field routing/agent checks).
- CLI (Typer): ops verbs `init`, `uninit`, `doctor [--repair]`, `migrate`,
  `rebuild`, `export`, `audit` (stub), `hook NAME` (shim entry point, no
  business logic yet). Agent verbs `start`, `done`, `fail --lesson`
  implemented for real; `brief`, `show`, `note`, `ask`, `wait`, `replan`,
  `status` are stubs. Human verbs (`approve`, `reject`, `ack`, `merge`,
  `close`, `pause`, `resume`, `handoff`, `import`) are stubs.
- `--request-id` idempotency (24h dedupe window) on `start`/`done`/`fail`;
  `done` is also a no-op when the node is already `done`.
- Note dedupe by content hash.
- Soft delete for nodes (`deleted_at`), never hard-delete.
- `init`/`uninit` install and precisely reverse absolute-path git hook
  shims (`.git/hooks/*` or `.husky/*` when present) and `.gitignore`
  entries, verified clean via `git status --porcelain` on 4 fixture repos.
- `rebuild`: replays `events` and diffs the result against the live DB
  (property-style tests over scripted and randomized operation sequences).

### Scope notes (see `docs/decisions.md`)
- Gates, criteria evaluation, granularity lint, `ask`/`wait`, plan
  revisions, the daemon/dashboard, drivers, structure-graph logic, and git
  hook business logic are out of scope for P0 (ship in P1+).
