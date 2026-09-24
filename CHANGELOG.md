# Changelog

All notable changes to this project are documented here.

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
