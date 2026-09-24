# Changelog

All notable changes to this project are documented here.

## [Unreleased] - P7 (drift loops, `audit`, lesson decay, real `drift_pct`, timeline scrubber)

### Added
- `core.drift` (plan section 9): the structure layer's drift machinery.
  `create_anchored_component` writes a real `{file_path: content_hash}`
  `anchors_json` (P6 left it at the `'[]'` schema default). Symbol-level/
  tree-sitter anchors stay explicitly deferred.
- **Drift loop item 1 (real, not P3's no-op signal):**
  `mark_stale_for_commit`, called from
  `core.hooks.handle_post_commit_from_git`, recomputes anchor content
  hashes on every commit and flips `components.status -> 'stale'` when
  an anchored file's content actually changed. Git rename detection
  (`git diff-tree -M`) retargets an anchor to a renamed file's new path
  instead of marking it stale on a pure rename. `component.updated`
  events carry the full row (rebuild-replayable, same convention as
  `node.*`/`project.*`).
- **Drift loop item 2:** `flag_unattributed_commit` -- a commit with no
  `Muvue-Node:`/`Refs:` trailer that touches an anchored file's path
  records an unacked `inbox.unattributed_commit` event, surfaced at `GET
  /inbox`'s new `"signals"`.
- **Drift loop item 3 ("reconcile-on-touch"):** `core.review.dispatch`
  now blocks/flags `done` (`review.stale_component_touched`) when a
  node's `predicted_touches` overlap a `stale` component's anchors,
  before any light/strict-mode-specific check runs.
- **Drift loop item 4:** `muvue audit [--n N]` (`core.drift.run_audit`),
  replacing the P0 `NOT IMPLEMENTED` stub -- samples the oldest-verified
  (or never-verified) components and drafts a proposed diff for each
  into the inbox (`inbox.audit_drift_signal`, `GET /inbox`'s
  `"audit_items"`). Also runs a lesson-decay pass each run.
- **Drift loop item 5:** `core.drift.drift_pct` -- the real `drift_pct`
  KPI (plan section 9: "% components verified within last K commits"),
  replacing the 0.0 stub `GET /kpis` shipped since P2.
- **Lesson decay:** `core.drift.record_lesson_retrieval` (called from
  `core.queries.brief_node` on every lesson surfaced) and
  `decay_lessons` -- a lesson note not retrieved by enough distinct
  projects is archived (`notes.archived_at`, a new `SCHEMA_VERSION 3`
  column) unless `pinned`. Archived lessons are excluded from `brief`.
- `core.migrate.run_migrate` now does a real `ALTER TABLE` step
  (idempotent via `PRAGMA table_info`, not error-catching) for
  `notes.archived_at` on a pre-existing database -- the first schema
  change since P0/P6 that isn't just a new `CREATE TABLE IF NOT EXISTS`.
- `core.rebuild`/`core.rebuild.diff_state` now also cover `components`/
  `notes` replay (previously projects/nodes only) -- P7 is the first
  phase to mutate either table outside `close`'s one-shot writes.
- `GET /events` gained `since`/`until` query params (an `events.ts`
  window, independent of the existing `since_id` cursor). The
  dashboard's timeline tab (`static/index.html`) got a client-side
  range-slider scrubber over its fetched event window.

### Scope notes (see `docs/decisions.md` #59-#65)
- `node_touches` (the real per-node structure-graph join table) still
  has no populated writer; reconcile-on-touch reuses `predicted_touches`
  instead, as the plan's own prompt names as the fallback.
- `components` has no creation/verification timestamp column; `audit`'s
  "oldest-verified" ordering is `(verified_sha IS NULL) DESC, id ASC` as
  a documented proxy.
- `drift_pct`'s formula is implemented exactly as plan section 9 states
  it ("% components verified"), even though that reads as a freshness
  metric under a field named for drift.

## [Unreleased] - P6 (close, structure layer, history archive, brief-reads-structure)

### Added
- `muvue close PROJECT_ID [--yes]` (`core.close.close_project`, plan
  section 9): real implementation, replacing the P0/P3/P5 `NOT
  IMPLEMENTED` stub. Without `--yes`: dry-run preview of the proposed
  structure diff (also `GET /projects/{id}/close-preview`); with
  `--yes`: commits the diff into `components`/`decisions`, writes the
  full current tables to `.muvue/components.json`/`.muvue/decisions.json`
  and commits them on the repo's `main` (muvue as "the single writer"),
  sets `phase -> closed`, exports the project's event history. Gated on
  every live `task`/`subtask` node being `done`. Diff is capped at
  `core.close.MAX_DIFF_ITEMS` (20) per category: `decisions` (from
  `kind='decision'` notes), `promoted_lessons` (from `pinned` `kind
  ='lesson'` notes, also landing in `decisions`), `components` (from
  distinct `predicted_touches` path globs not already tracked). Also
  `POST /projects/{id}/close`.
- `core.history` (plan section 2 file layout): the real per-project
  `.muvue/history/<id>.jsonl.gz` archive (`export_project`) --
  gzip-compressed JSONL, replacing `export`'s P0-era whole-DB flat dump
  for the per-project case (`muvue export --project-id ID`, and what
  `close` calls internally). `rebuild_from_archive` replays one
  project's archive in isolation.
- `core.rebuild.rebuild_state_from_events`: the event-fold logic
  `rebuild_state(conn)` used inline, now factored out and shared with
  `core.history.rebuild_from_archive` -- one project's exported archive
  replays through the exact same fold as a full-DB replay.
  `core.rebuild.diff_project_from_archive` / `muvue rebuild --project-id
  ID [--from-archive PATH]`: project-scoped counterpart to `diff_state`
  (P6 acceptance #3).
- `core.queries.brief_node` now also returns `relevant_decisions`/
  `relevant_components` (plan section 4: `brief`'s ranking "over notes,
  decisions, component purposes"): FTS5 search
  (`search_decisions`/`search_components`) against new
  `decisions_fts`/`components_fts` virtual tables (`SCHEMA_VERSION = 2`)
  built from the node's own title/body, `bm25()`-ranked, `status
  ='current'` only. A brand-new project's `brief` now surfaces a
  decision `close`d on a completely unrelated, already-`closed` project
  (P6 acceptance #1) -- the FTS5 index is global, not project-scoped.
- `core.imports.import_github_issue` / `muvue import --from github#N
  --node-id ID [--data PATH]` (plan section 4/11, human verb): links a
  node to a GitHub issue/PR in `external_refs`. No live GitHub API
  access in this environment (plan working rule 2) -- issue data comes
  from an inline dict, a local JSON file (`--data`), or an injected
  `fetch_fn(issue_number)` seam a real implementation plugs into later.
  Also `POST /import`.
- `core.pr.generate_pr_body` / `muvue merge NODE_ID --pr` (plan section
  4/6/11): generates a PR description body (markdown) -- the node's
  title/body, acceptance criteria, relevant decisions (same FTS5 search
  `brief` uses), notes, and linked `external_refs`. No real `gh pr
  create` call (no network access here); returned as text. Also `POST
  /nodes/{id}/merge?pr=true`.

### Changed
- `SCHEMA_VERSION` 1 -> 2: adds `decisions_fts`/`components_fts` FTS5
  virtual tables + their AI/AD/AU triggers (same pattern as `notes_fts`).
  Run `muvue migrate` on an existing `.muvue/muvue.db`.

## [Unreleased] - P5 (runner, drivers)

### Added
- `muvue run [--agent X] [--parallel N] [--project-id ID]`
  (`core.runner.run`): unattended runner. Spawns one configured driver
  subprocess per ready `task`/`subtask` node with `brief` piped on stdin
  (fresh subprocess per node, no shared process state), records real
  usage to `node_usage`, and applies `[routing]`/`[budget]`/
  `on_rate_limit` policy from `config.toml`. Reconciles expired leases and
  `blocked(rate_limit)` nodes whose `retry_at` has passed at the top of
  every run (extends `core.daemon`'s reconcile-on-start pattern). Pauses
  (returns control, never crashes) when it finds a node already in
  `awaiting_approval`/`blocked`/`failed`, when a node it just processed
  lands in `failed`/`review`/`blocked(rate_limit)`/`blocked(external)`,
  or when the budget is exhausted; warns once (a `runner.budget_warning`
  event) at 80% spend.
- `core.drivers`: the real invocation layer. `invoke_driver(agent_name,
  agent_cfg, brief_text, cwd)` runs `auth_check` (if configured, treating
  a failure as `status="unavailable"`, never a crash), then spawns
  `command` (`shell=True`, `brief` on stdin), and parses usage via
  `usage_parser`: `claude_stream_json`, `codex_json`, `gemini_json`
  (**synthetic/unverified** -- see `docs/providers.md`), and `fake`
  (real, tested against a real subprocess). Never reads, stores, or
  forwards any credential (plan principle 7) -- only shells out and
  inherits the caller's own environment.
- `src/muvue/fake_agent.py` / `muvue-fake-agent` console script (new
  `pyproject.toml` entry point): a real, invocable subprocess stand-in
  for a subscription-authenticated vendor CLI, scriptable via
  `--behavior`/`MUVUE_FAKE_BEHAVIOR` (`cooperative`, `lazy`,
  `adversarial`, `rate_limited`, `crash`, `failed`) and `--check`/
  `MUVUE_FAKE_AUTH_FAIL` for `auth_check` simulation. Distinct from P3's
  `tests/fake_agent.py`, which stays an in-process `core`-driving test
  fixture.
- `core.merge` (plan section 6 "Merging"): `attempt_merge`/
  `merge_pending`. Merges a `done`, strict-mode node's branch onto the
  airlock's `main` in dependency order. On conflict: node ->
  `blocked(conflict)`, `attempts + 1`, a "rebase onto main" subtask
  auto-created under it. Light-mode / never-strict-started nodes are a
  documented no-op. `muvue merge [NODE_ID]` / `POST /nodes/{id}/merge`.
- `core.nodes.handoff(new_owner=)` (plan section 6 "Handoff"): reassigns
  a node's lease (un-blocking it first if `blocked`) so a different
  driver -- interactive session <-> the unattended runner -- can resume
  purely from DB state. Human verb, never exposed over MCP. `muvue
  handoff NODE_ID --to OWNER` / `POST /nodes/{id}/handoff`.
- `state_machine.TRANSITIONS`: new `(done, blocked)` edge, no owner
  required -- the one exception to "done is terminal," needed for
  `core.merge`'s post-done conflict detection.
- `core.nodes.block(..., bump_attempts=, event_actor_role=)`: additive
  optional parameters (default preserves every pre-P5 call site's exact
  behavior) so `core.merge`'s conflict handling can bump `attempts` and
  attribute the event to `daemon` instead of `agent`.
- `GET /kpis`: `tokens_per_node`/`spend_vs_budget` are now real (read
  `node_usage`, populated by the runner), no longer stubbed at zero.
- `tests/fixtures/vendor_samples/`: synthetic/unverified recorded-output
  samples for `claude_stream_json`/`codex_json`/`gemini_json`, labeled as
  such (no network access / logged-in vendor CLI in this environment).

### Fixed
- `core.nodes.done`'s `review.awaiting` branch (P2) set `nodes.summary`
  without ever recording a `node.`-prefixed event carrying it, so
  `rebuild` silently dropped the summary on any flagged node -- found by
  P5's rebuild-first test for the runner's `done` flow. Now also records
  `node.summary_recorded` (full row snapshot) alongside `review.awaiting`.

## [Unreleased] - P4 (strict mode)

### Added
- `muvue.core.strict`: the airlock bare repo (`~/.muvue/airlocks/<repo-
  hash>.git`, hash = first 16 hex chars of sha256 of the repo's resolved
  absolute path -- see `docs/decisions.md` #41), one git worktree per
  node under `~/.muvue/worktrees/<repo-hash>/node-<id>`, and a real
  `pre-receive` hook installed on the airlock.
- `core.nodes.start(..., config=, repo_root=)`: when `config.mode ==
  "strict"`, binds a per-node worktree (via `core.strict.bind_worktree`)
  *before* the node's status transition is applied, and runs
  `config.worktree_setup` once in it. A `worktree_setup` failure raises
  `StrictModeError` with the command's stdout/stderr attached and leaves
  the node untouched (still `ready`, `worktree` still `NULL`) and no
  orphan worktree on disk. Light mode (`config=None`, or
  `config.mode == "light"`) is unaffected -- no worktree logic runs.
- `muvue hook pre-receive`: a real pre-receive hook (reads git's
  `<old> <new> <ref>` protocol lines from stdin), installed automatically
  on the airlock by `core.strict.ensure_airlock`. Rejects any push to
  `refs/heads/main` (main is never a direct push target in strict mode)
  and any push to a `refs/heads/node-<id>` branch whose node doesn't
  exist, has no bound worktree, or isn't `in_progress`/`review`. This is
  the mechanism behind P4 acceptance criterion 1.
- `core.review.dispatch`: extended for `config.mode == "strict"`. When a
  node has a bound worktree, `auto` criteria now run in that worktree
  (clean-env checks, plan section 5) instead of the caller's `cwd`, and
  the dispatch additionally flags the node to `review` when a real
  `git diff --name-only` (`core.strict.worktree_diff_files`, three-dot
  against `main`) touches a test-shaped path (`core.risk.is_test_touch`,
  reused from light mode). This is P4 acceptance criterion 2. A
  strict-mode node with no bound worktree still no-ops exactly as before
  P4 (unchanged: `tests/test_light_review.py::
  test_dispatch_is_a_noop_outside_light_mode`).
- `core.doctor.run_doctor`: strict-mode check -- any `in_progress`/
  `review` node must have a bound worktree that still exists on disk and
  is never `repo_root` itself ("agent never holds a `main` checkout",
  plan section 5).
- `muvue init --sandbox`: additionally writes `.muvue/sandbox-compose.yml`,
  a documented, unimplemented-runtime compose scaffold (plan section 5:
  "`init --sandbox` emits a compose file for container isolation
  (later)"). Scaffold only -- muvue does not build, start, or manage it;
  today's strict-mode isolation is the git-worktree/airlock mechanism
  above. `init` without `--sandbox` is unchanged.

### Notes
- `protocol_version` is **not** bumped: `start`/`done`'s verb contract
  from a caller's perspective is unchanged (new `config`/`repo_root`
  kwargs on `core.nodes.start` are internal wiring the CLI/API already
  had `config` for; no new required argument, no changed return shape).
  See `docs/decisions.md` #44.
- Per plan section 13 (residual risks) and section 5's own framing:
  strict mode's pre-receive check is state-based only (does the pushed
  ref correspond to a live node binding?) -- it cannot verify which
  process or worktree a push actually originated from on a single
  machine. **Documented guarantee: prevents accidental and lazy bypass,
  not adversarial isolation.**
- Out of scope for P4 (plan section 12 working rule 7): no runner/
  drivers, no `close`/history-export, no structure-graph/drift/`audit`
  logic, no VS Code extension, and `--sandbox` stays scaffold-only (no
  container orchestration logic was written).

## [Unreleased] - dogfood gate (plan section 11, post-v0.1)

### Added
- `muvue project create --goal TEXT [--budget-unit UNIT] [--budget-limit
  N]`, `muvue spec PROJECT_ID --title TEXT --body TEXT` (agent verb,
  Gate 1), `muvue decompose SPEC_ID --title TEXT [--criteria ...]
  [--predicted-touches ...] [--criteria-mode auto|external|manual]`
  (agent verb, Gate 2): the dogfood gate run (plan section 11) failed at
  ~62% because these had no CLI verb at all -- only raw `core.projects.
  create_project` / `core.gates.submit_spec` / `core.nodes.create_node`
  Python calls, forcing anyone using muvue to bypass the CLI for its own
  planning surface. See `docs/decisions.md` #39.
- `muvue approve review:ID` (`core.nodes.approve_review`): the API
  already dispatched this target; the CLI's `approve` command was
  missing the branch and the `--help` text. Added and covered by a CLI
  subprocess test.
- `core.repo_init._update_gitignore` no longer duplicates a `.muvue/*`
  entry inside its marker block when that exact line already exists as
  plain (unmarked) text in the repo's `.gitignore`. See
  `docs/decisions.md` #40. Driven end-to-end through the new `project
  create`/`spec`/`decompose` CLI verbs as this cycle's dogfood project.
- CLI `done --run-checks` and API `POST /nodes/{id}/done {"run_checks":
  true}`: opt-in wiring of `core.review.default_run_checks` (P3 decision
  #38's flagged follow-up). Omitted/false preserves risk-tier-only gating
  exactly as before; set, a failing `config.checks.test` always flags the
  node to `review` even at low risk tier.
- `muvue answer QUESTION_ID --text TEXT` (CLI) and `POST
  /questions/{id}/answer` (API, session-token gated): `core.asks.answer`
  had zero entry point since P1 (`docs/protocol.md` flagged it explicitly
  — no human-verb surface was ever specified for it, and the dashboard
  inbox that P2 was meant to ship it through never wired an answer
  action). `core.asks.HumanOnly` now also refuses a non-`human` actor
  calling `answer` directly against core, matching `core.gates.HumanOnly`/
  `core.nodes.HumanOnly`. `answer` is correctly never exposed over MCP.

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
