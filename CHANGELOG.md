# Changelog

All notable changes to this project are documented here.

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
