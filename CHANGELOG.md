# Changelog

All notable changes to this project are documented here.

## [Unreleased] - v4 delta closure, W4: human/agent control surface

### Added
- **Real `pause`, `resume`, `reject` and `ack` in the CLI.** They
  replace the "not implemented" stubs, which contradicted decision #26.
  `reject review:ID --feedback TEXT` sends a node in review back to
  `in_progress` with the feedback as a note.
- **`pause` stops running agents.** It sends SIGTERM to every `muvue
  run` process registered for the repo (`.muvue/runners/<pid>.json`).
  The runner then kills each driver's process group, releases its
  leased nodes back to `ready` without using up an attempt
  (`node.released`), and exits with reason `stopped` (decision #122).
- **Agent output is logged.** Drivers stream their output to
  `.muvue/logs/<node>.log`, which is gitignored.
- **Invoker detection** (`core.actor`, decision #123, supersedes #73).
  A human verb whose caller descends from `claude`, `codex`, `gemini`,
  `cursor-agent` or `aider` is recorded as `actor=agent` with
  `actor_evidence=agent_parent:<name>`. It is still allowed: this is
  detection, not prevention. A call with no TTY is recorded as
  `no_tty`.
- **`--request-id` on every mutating verb.**
  - CLI: `--request-id` on every verb.
  - MCP: a `request_id` argument on `note` and `replan`.
  - API: an `X-Request-Id` header on every mutating endpoint that does
    not already take `request_id` in its body.
  - A repeated call returns `{"noop": true, "result": <first result>}`
    (#124).
- **`ask --default TEXT [--default-ok]`** (v4 section 4). `--default`
  is now required. `--default-ok` is stored on the question
  (`questions.default_ok`, schema 7), so `wait` applies the default on
  timeout without a flag of its own. `wait --default-ok` still works
  (#125).
- **`serve` writes `~/.muvue/daemon/<repo-hash>.json`.** The file is
  created with mode 0600, contains only `{"port", "pid"}`, and is
  removed when the daemon exits on SIGINT or SIGTERM.

### Changed
- **Editing criteria on an `in_progress` node** parks the node in
  `awaiting_approval` with its lease intact, and sets its tier to
  `high`. PreToolUse blocks edits until a human runs `approve task:ID`,
  which hands the node back to the same owner in `in_progress` (#126).
  Until now nothing ever set `awaiting_approval`.
- `POST /projects/{id}/pause` now returns `{"project",
  "stopped_runners"}`. `resume` on a project that isn't paused returns
  409.

## [Unreleased] - v4 delta closure, W3: data model and replay

### Added
- `decompose`/`replan --depends-on N` (repeatable) write `deps` edges,
  each logged as a `dep.added` event (decision #116). `project create
  --follows/--supersedes N` writes `project_links`.
- `muvue rebuild --apply`: backs up `.muvue/muvue.db` to
  `muvue.db.bak-<UTC>`, then rewrites the replayable tables from the
  event log (#119).
- `tests/test_txn_discipline.py`: an AST check that every `execute` call
  in the package sits inside `read_txn`/`write_txn` (#120).

### Changed
- Replay (`rebuild`, `diff_state`) now also covers `deps`,
  `node_commits`, `actual_touches`, plan-revision approvals and
  `agent_spend`, the rest of plan section 3's replayable list (#119).
- `fail` requires the full lesson: `--lesson` (the failure), `--trigger`,
  `--do-instead` and `--scope`, in the CLI, MCP and API. A `lesson` note
  must be the structured form (#118).
- `export` without `--project-id` writes one `.jsonl.gz` archive per
  project plus `unscoped.jsonl.gz`, not `events.json` (#121).
- Every read runs inside `read_txn` (new `db.query_one`/`query_all`
  helpers), and `read_txn` raises if a write happens inside it.

### Removed
- The v3 per-project budget: `projects.budget_unit/budget_limit/spent`
  (schema 6; `migrate` drops them) and `project create --budget-unit/
  --budget-limit` (#117). Budgets are per driver.

## [Unreleased] - v4 delta closure, W2: hook fast path

### Changed
- **SessionStart, Stop and PreCompact decide again** (decision #114,
  supersedes #80). SessionStart prints the active node's brief as
  session context. Stop blocks (exit 2, reason on stderr) while the
  active `in_progress` node has no note logged since its latest
  `start`, and allows while `stop_hook_active` is set. PreCompact blocks
  on the same condition unless the payload has a `summary`. All three
  still spool their event.
- **PreToolUse output follows Claude Code's contract.** A block exits 2
  with the reason on stderr (it used to print JSON to stdout, which
  Claude Code ignores on exit 2). An allow prints nothing.
- **`core.claude_hooks` removed.** `muvue hook NAME` now runs the same
  `muvue._hook.run` code the installed shims run.
- **The daemon drains the hook spool continuously** in a background
  task, with or without a dashboard connected (#115). The SSE stream no
  longer drains.
- **`doctor` always reports `hook queue depth: N`**, and the CLI's
  catch-up drain no longer runs before `doctor`, so the depth is what
  was actually queued.

### Fixed
- **The 150ms hook deadline was only checked after the query.**
  `sqlite3.connect` kept its default 5s lock wait, so a locked DB could
  hang a tool call. The lock wait is now capped at the time left, and a
  progress handler interrupts a query that runs past the deadline.

### Added
- `tests/test_hook_latency_benchmark.py` times PreToolUse's DB path
  (an `Edit` on an `in_progress` node) against the 150ms deadline, and
  the gate's "median added latency per tool call" now uses that
  worst case. Local numbers: DB path p50 21.0ms, p95 25.1ms, p99 27.0ms;
  gate median 23.8ms.

## [Unreleased] - v4 delta closure, W1: confirmed bugs

A full audit of the code against the v4 handoff plan found about 100
deltas. This section covers the ones that were outright bugs.

### Fixed
- **Restarting `serve` cleared the inbox.** `reconcile_on_start` called
  `process_queue`, which set `acked_at` on the oldest 100 unacked events
  of any type. The inbox treats "unacked" as "open", so every restart
  silently closed unattributed-commit, signal and audit items.
  `process_queue` is gone; reconcile now reverts expired leases and
  drains the hook spool, and never touches `acked_at`
  (docs/decisions.md #111).
- **Hook lines appended during a queue drain were lost.** `drain_queue`
  read `queue.jsonl`, processed it, then overwrote it with the
  remainder, so a line a hook appended mid-drain vanished, and two
  drainers (CLI callback plus daemon) clobbered each other. Drains now
  take an `flock` on `.muvue/queue.lock` (a second drainer backs off),
  atomically rename the spool to `queue.draining`, and carry over any
  bytes that land after the read (#112).
- **Strict `start` discarded earlier merges.** `ensure_airlock`
  force-fetched `+HEAD:refs/heads/main` from the checkout on every
  `start`, rewinding the airlock's `main` past merge commits, which
  `merge.completed` then stopped from ever being re-merged. The fetch
  now lands on `refs/muvue/upstream`; `main` is seeded when missing and
  otherwise only fast-forwarded (#113).
- **`/events` returned the oldest events.** Without a `since_id`
  cursor it now returns the newest `limit` events, oldest first; with a
  cursor it still pages forward.
- **The plan's own example `config.toml` failed to load.** Added
  `[daemon] bind`/`allowed_origins` (wired into `serve`'s default host
  and the Origin check), `planning.require_auto_criterion_above_tier`
  (wired into the Gate 2 hard block) and `cost_model = "requests"`.
  `tests/fixtures/v4_spec_config.toml` is the plan's block copied
  exactly.
- **Request-id dedupe ran outside the write transaction** in `start`,
  `done`, `fail` and `ask`, so two concurrent calls with the same id
  could both apply. The check now runs inside `BEGIN IMMEDIATE`.
- **`muvue run` crashed on a paused project.** The runner now skips
  paused projects' nodes, stops with `paused.reason = "project_paused"`
  when its project is paused, and reports `project_paused` for a pause
  that lands between scheduling and `start`.
- **`is_test_touch` matched any path containing "test"** (`latest.py`,
  `attestation.py`). It now matches test directory segments and
  conventional test file names only.

## [Unreleased] - v4 §11 P0/gate re-verification + `uninit` acceptance tightening (changelog item 14, final v4-migration piece)

Branch `feat/v4-uninit-and-gate-recheck`, built on every prior v4-delta
branch. Closes v4 changelog item 14 part 1 ("`uninit` acceptance made
testable") and re-verifies the P0/dogfood-gate acceptance criteria
against v4's tightened §11 wording (both predate or were written before
several later v4-delta sessions and needed a real re-check, not an
assumption).

### Fixed
- **`.muvue/.init_manifest.json` was never gitignored.** `core.repo_init.
  _update_gitignore` now adds it to the marker-block entries. Without
  this, an ordinary mid-project `git add -A && git commit` would sweep
  the backup manifest `uninit_repo` needs into git history; a later
  `git reset --hard` (or anything else reverting that commit) could
  delete or stale it, and `uninit_repo` silently treats a missing
  manifest as "nothing to restore" rather than erroring -- so `uninit`
  would quietly leave original hook-file contents unrestored and still
  report success. Found by the new filesystem-snapshot round-trip test
  below; see docs/decisions.md #109.

### Added
- **`uninit_repo` now also deletes the `muvue/structure` git ref
  (`refs/heads/muvue/structure`) if one exists**, via `git update-ref -d`
  (never touches the working tree, index, or `HEAD`), skipping only if
  it's the ref currently checked out. v4's P0 acceptance wording predates
  §9's structure-ref mechanism and doesn't say either way; judged
  muvue-owned the same way `.muvue/` is. See docs/decisions.md #108.
- **`tests/test_init_uninit.py` rewritten**: `init` -> real usage (create
  a project/node, commit, run the post-commit hook, write a structure
  commit onto `muvue/structure`) -> `uninit`, asserted against a real
  before/after filesystem snapshot (every file on disk, not just `git
  status --porcelain`'s view of it) on all 4 fixtures (`plain_python`,
  `js_husky`, `docs_only`, `monorepo`). This is the "made testable" half
  of changelog item 14: v3/pre-tightening acceptance was satisfiable even
  if `uninit` left a gitignored file behind, since gitignored files never
  show in porcelain output; this doesn't have that blind spot. All 4
  fixtures round-trip byte-identical (`.git/index`/`objects`/`logs`/
  `COMMIT_EDITMSG`/`ORIG_HEAD` excluded as git-internal bookkeeping
  unrelated to what muvue touched -- see docs/decisions.md #110).
- **`tests/test_hook_latency_benchmark.py`: new
  `test_gate_median_added_latency_per_agent_tool_call`.** v4 §11's gate
  row added a *new* criterion beyond v3's 80%-logged-unprompted bar:
  "median added latency per agent tool call < 100 ms". Computed from a
  fresh 60-sample set of real cold `muvue._hook` subprocess invocations
  (same measurement `_time_one_invocation` already made for p95/p99,
  reduced to median here). Measured locally (no CI runner available in
  this environment): **median 18.9ms**, comfortably under the 100ms bar
  (samples ranged 17.5-24.0ms this run). Not a new mechanism -- P0.5's
  hook fast path already existed; this is the re-verification v4 working
  rule 9 asks for ("verify and report, don't assume an adjacent
  measurement satisfies a differently-worded new criterion").

### Verified, no change needed
- **Original P3 gate's 80%-logged-unprompted property.** Spot-checked
  every v4-delta session's new functionality (per-driver budgets,
  `--parallel` restriction, branch coherence, granularity hard-block,
  `touches_outside_predicted`, `muvue/structure` commits) against
  `cli/main.py` and `api/app.py`: each is reachable through an existing
  CLI/API verb (`run --parallel`, `start`, `decompose`/`propose-revision`,
  `close`) -- none is a brand-new human/agent verb that exists only as a
  raw `core.*` call, the class of gap the original gate exercise found
  and fixed. No new CLI/API wrapper needed.

## [Unreleased] - v4 §7: commit-trailer enforcement relocation (changelog item 10)

Branch `feat/v4-trailer-enforcement-relocation`, built on §1.2/§3, §4a,
§8a, the §5 enforcement deltas, and the runner/budget deltas. v4 §7 /
changelog item 10: "commit-trailer enforcement moved from `PreToolUse`
string matching to `post-commit` detection plus strict-mode
`pre-receive`."

### Removed
- **`PreToolUse`'s `git commit`-without-trailer string-match block.**
  `core.claude_hooks.pre_tool_use` and `muvue._hook._decide` no longer
  block a `Bash` tool call for a missing/incorrect `Muvue-Node:`/`Refs:`
  trailer. v4's position: matching a shell command's text is defeated by
  `git -C`, heredocs, chained commands, aliases and scripts, and
  produces false positives on any string merely containing "git commit"
  -- this is removed as unsound, not fixed with a better regex. The
  `Edit`/`Write` blocking behavior (no `in_progress` node, or an
  `awaiting_approval` one) is unchanged. `Bash` was also dropped from
  `muvue._hook._BLOCKING_TOOL_NAMES`, so a `Bash` `PreToolUse` call no
  longer opens the DB at all (nothing else needed it). This is a
  deliberate behavior *reduction* -- see
  `tests/test_trailer_enforcement_relocation.py` and
  `tests/test_adapters.py`/`tests/test_hook_fast_path.py`'s renamed
  tests, which now assert the allow, not the block.

### Added
- **`core.drift.flag_general_unattributed_commit`.** Generalizes P7's
  drift-loop-item-2 unattributed-commit signal
  (`flag_unattributed_commit`, kept unchanged, component-anchor-scoped
  only), fired unconditionally from `core.hooks.handle_post_commit` for
  *every* commit whose `Muvue-Node:`/`Refs:` trailer is missing or
  doesn't resolve to a real, non-deleted node -- not just commits that
  touch an anchored structure component. Records a distinct, unacked
  `unattributed_commit` event (separate from the existing
  `inbox.unattributed_commit` type).
- **`GET /inbox`'s new `"unattributed_commits"` list.** Surfaces unacked
  `unattributed_commit` events, alongside the existing `"signals"`
  (still `inbox.unattributed_commit` only).

### Scope notes (see `docs/decisions.md` #100)
- Strict mode's real barrier, `pre-receive` (`core.strict`), is
  untouched -- confirmed unaffected by this change, remains the
  load-bearing prevention mechanism in strict mode; the post-commit
  detection added here is advisory/detection-only in both modes, same
  as light mode always was.
- Did not touch `core.close.py`, `.muvue/components.json`/
  `.muvue/decisions.json` commit logic, structure snapshots, daemon
  security, budgets, or `--parallel` -- out of scope, owned by other
  (some concurrent) sessions.

## [Unreleased] - v4 §9: structure commits via a `muvue/structure` ref (changelog item 7)

Branch `feat/v4-structure-ref-commits`, built on §1.2/§3, §4a, §8a, the
§5 enforcement deltas, and the runner/budget deltas. v4 §9 / changelog
item 7: "structure commits via `muvue/structure` ref and a temporary
index instead of committing to a checked-out `main`."

### Changed
- **`core.close.close_project` no longer commits `.muvue/components.json`
  / `.muvue/decisions.json` directly onto whatever branch `repo_root` has
  checked out.** The P6/v3-era implementation ran a plain `git add` +
  `git commit` on the repo's real working tree and index, which races the
  user's own uncommitted work and index lock (v4 §9's own framing of the
  defect). Instead, `close_project` now builds the structure commit with
  `core.close._write_structure_commit`: a temporary index
  (`GIT_INDEX_FILE`, a fresh `tempfile.mkstemp()` path per call) seeded
  from `refs/heads/muvue/structure`'s current tree (or `HEAD`'s tree, on
  the first-ever structure commit), with the two JSON files' new content
  written straight into git's object database via `git hash-object -w
  --stdin` -- `repo_root`'s real `.git/index` and working tree are never
  read or written by this step. The resulting commit lands on
  `refs/heads/muvue/structure` via a compare-and-swap `git update-ref`
  (decision #104).
- **`main` is fast-forwarded only when it's genuinely safe.**
  `core.close._maybe_fast_forward_main` fast-forwards `main` (`git merge
  --ff-only refs/heads/muvue/structure`, decision #102) only when
  `repo_root`'s checked-out branch is literally `main` *and* `git status
  --porcelain` is empty. In every other case (a different branch checked
  out, or `main` but dirty), `main` and the working tree are left
  completely untouched, and `close_project` records an unacked
  `inbox.structure_update_ready` event (payload: `ref`, `sha`, `reason`,
  a human-readable `message`) describing where the structure commit
  landed and that it needs a manual `git merge refs/heads/muvue/structure`
  or a PR.
- **`close_project`'s return dict gained `structure_ref`, `structure_sha`,
  `fast_forwarded`, and `inbox_event_id`** (the last `None` when a
  fast-forward happened). `components_path`/`decisions_path` are still
  returned (the intended `repo_root/.muvue/{components,decisions}.json`
  paths) but the files at those paths now only actually exist on disk
  when `fast_forwarded` is `True` -- callers that need the diff content
  regardless of fast-forward outcome should read `diff_committed` from
  the same result, or `git show refs/heads/muvue/structure:.muvue/components.json`.

### Tests
- `tests/test_close.py`: new `test_structure_commit_never_touches_real_index_or_working_tree`
  (direct unit test of `_write_structure_commit` against a repo with real
  staged *and* unstaged uncommitted changes, asserting byte-identical
  `git status --porcelain` and file content before/after),
  `test_close_project_clean_main_fast_forwards`,
  `test_close_project_non_main_branch_leaves_inbox_item`,
  `test_close_project_dirty_main_leaves_inbox_item_and_working_tree_untouched`.
  The pre-existing P6 `test_close_project_confirmed_writes_and_commits`
  is kept and still passes unchanged in outcome (its fixture repo is on
  `main` with a clean tree, so it hits the fast-forward path), with
  updated assertions/docstring making the mechanism change explicit
  (decisions #106, #107 cover the fixture changes this required).
## [Unreleased] - v4 §2/§6/P5: per-driver budgets, --parallel restriction, rate-limit wait timeout

Branch `feat/v4-runner-budget-deltas`, built on §1.2/§3 (txn discipline,
`agent_spend` table skeleton, decision #72), §4a, §8a, and the §5
enforcement deltas. Four deltas: v4 changelog items 4, 5, 11, and P5's
row in §11 as amended by v4.

### Changed
- **Config schema (v4 §2, changelog item 5).** The single top-level
  `[budget]` (`unit`/`limit`) is gone. It's now unit-free stop
  conditions only: `max_wall_clock_minutes` (default 240),
  `max_nodes_per_run` (default 20) (`core.config.BudgetConfig`). Each
  `[agents.<x>]` gains an optional nested `[agents.<x>.budget]`
  (`core.config.AgentBudgetConfig`: `unit`, `limit`) -- `None` means
  unlimited for that driver. `AgentConfig` also gains `max_wait_minutes`
  (default 30) and `on_rate_limit_timeout` (default `"pause"`, validated
  to `"pause"` or `"fallback:<agent>"` -- `"wait"` is rejected, since a
  *timeout* action of `"wait"` would be the unbounded stall
  `max_wait_minutes` exists to prevent). `DEFAULT_CONFIG_TOML` and this
  repo's own dogfood `.muvue/config.toml` migrated to the new shape.
- **`doctor` budget-unit validation (v4 §2).** `core.doctor.run_doctor`
  now errors (not warns) when a configured `[agents.<x>.budget].unit`
  isn't producible by that driver's `cost_model`
  (`core.runner.EXPECTED_BUDGET_UNIT`, mirroring decision #72's existing
  `usd -> usd` / `tokens -> tokens` / `quota -> requests` mapping, not a
  new one).
- **`core.runner`: per-driver budget enforcement (v4 §2/§6).**
  `core.runner.budget_state` (single global spend-vs-limit) is replaced
  by `driver_budget_state`/`driver_budget_states` (per agent, summing
  `agent_spend` across every project for that agent+unit against its own
  `[agents.<x>.budget]`). `run()` computes the set of 100%-exhausted
  agents each cycle and passes it to `select_batch`, which now skips any
  node routed to an exhausted agent -- other agents keep being scheduled
  normally. An agent at >= 80% logs one `runner.driver_budget_warning`
  event (once per agent per run). If every remaining ready node routes
  to an exhausted agent, `select_batch` simply returns nothing and the
  run stops as a natural consequence of "nothing left to schedule" -- no
  separate "no path left" detection was built, per the plan's own
  wording. The unit-free `[budget]` (`max_wall_clock_minutes` via an
  injectable `now_fn`, `max_nodes_per_run` via `len(processed)`) is
  checked independently, at the top of every cycle, before any
  driver-budget or gating check. `run()`'s returned `"budget"` field is
  now `{agent_name: state, ...}`, not one global dict.
- **`--parallel N > 1` refused outside `worktree_mode = "per_node"` (v4
  §6, changelog item 4).** New `core.runner.ParallelismRefused` /
  `validate_parallel`, called first thing inside `core.runner.run` --
  before any DB connection or side effect. The CLI (`muvue run`) catches
  it and exits 1 with a clear message. `--parallel 1` (default) is never
  restricted, in either `worktree_mode`. `select_batch`'s
  `predicted_touches`-disjointness scheduling is unchanged in code but
  re-framed in docs as a merge-conflict-reduction heuristic, not a
  safety property -- it can now only ever matter when
  `worktree_mode == "per_node"`, since that's the only way `parallel >
  1` reaches it at all.
- **`max_wait_minutes` bounds `on_rate_limit = "wait"` (v4 §6, changelog
  item 11).** `core.runner._apply_rate_limit` now tracks
  `wait_started_at` per rate-limit "episode" (carried forward across
  `reconcile_rate_limits`'s unblock-and-retry cycles via the latest
  `runner.rate_limited` event, unless something other than a plain
  `node.ready`/`node.start` happened in between, which starts a fresh
  episode). Once `now - wait_started_at >= max_wait_minutes`,
  `on_rate_limit_timeout` applies instead of continuing to wait, and a
  `runner.rate_limit_wait_exhausted` event fires as the notification (no
  real notification-sending exists outside the dashboard yet, so a
  `write_txn`-recorded event is the documented minimum-acceptable
  implementation -- see docs/decisions.md).

### Tests
- `tests/test_v4_budget_config.py`, `tests/test_v4_doctor_budget_validation.py`,
  `tests/test_v4_rate_limit_wait_timeout.py` (new).
- `tests/test_runner.py`: budget tests rewritten to the per-driver shape
  (`config.agents["fake"].budget = AgentBudgetConfig(...)` instead of
  `config.budget.unit`/`.limit`); new two-driver
  exhausted-vs-generous test, `max_nodes_per_run`/
  `max_wall_clock_minutes` tests, `--parallel` refusal/`per_node`-mode/
  `parallel=1`-in-either-mode tests.
- `tests/test_run_cli.py`: `test_run_cli_respects_parallel_flag`
  (assumed the old "accepted, silently serial" behavior) replaced with
  three tests proving the v4 refusal/allow/parallel-1 behavior.

## [Unreleased] - v4 §5: enforcement deltas (granularity hard block, touch-drift tier, branch coherence)

Branch `feat/v4-enforcement-deltas`, built on §1.2/§3 (txn discipline),
§4a (hook fast path) and §8a (daemon security). Three independent v4 §5
deltas, changelog items 9/(P2b acceptance)/12.

### Changed
- `core.gates.approve_node` (changelog item 9): a medium- or high-tier
  task/subtask node whose `criteria_mode != "auto"` is now **refused**
  (`GateError`), not merely warned by `lint_task` -- "otherwise an agent
  closes the loophole by declaring every criterion `external` and
  self-attesting" (v4 §5). Checked on every `approve_node` call, both
  the initial Gate 2 freeze and re-approval after a criteria edit (a
  criteria edit that keeps a node non-auto and forces it to `high` via
  `core.gates.edit_criteria` hits the same block on re-approval).
  Matches plan §11's literal P1 acceptance bar: "all-`external`
  medium-tier task is refused." Low tier is unaffected -- still only
  warned, as before. "No auto criterion" reads at the same node-mode
  granularity `lint_task`'s pre-existing warning already used
  (`criteria_mode != "auto"`; docs/decisions.md #12), now applied as a
  decision entry #88.
- `core.risk.compute_tier`: new `touches_outside_predicted` input (v4
  §5's risk-tier inputs list). `core.risk.touches_outside_predicted(conn,
  node_id)` compares a node's `actual_touches` (real commits, written by
  `core.hooks.handle_post_commit`) against its `predicted_touches` globs;
  a real touch matching no predicted glob raises the tier to at least
  `medium`, never lowers it. Wired into `core.nodes.done`'s tier
  recompute (when `config` is given) -- the point at which real commits
  and their `actual_touches` rows actually exist -- not into Gate 2
  approval, which has no commit history yet.
- `core.nodes.start` / `core.doctor.run_doctor` (changelog item 12):
  branch-coherence check. `core.projects.create_project` now records the
  branch checked out in `repo_root` (via new `core.gitutil.
  current_branch`) into new `projects.branch` (SCHEMA_VERSION 4 -> 5).
  Every `start` (CLI, API, MCP -- all three now pass `repo_root`/`config`
  through) and every `doctor` run compares the repo's *own working
  tree's* current branch (never a strict-mode per-node worktree's, which
  is intentionally on its own `node-<id>` branch) against the recorded
  one: light mode records a `branch.diverged` event and proceeds;
  strict mode refuses `start` (`NodeError`) before any worktree bind,
  and `doctor` reports it as a failing issue (light mode: a warning).

### Added
- `core.gitutil.current_branch(repo_root)`: the one `git rev-parse
  --abbrev-ref HEAD` helper shared by all three of the above call sites.
- `projects.branch` column (SCHEMA_VERSION 5); `core.migrate` `ALTER
  TABLE`s it into pre-existing databases; `core.rebuild`'s replayable
  project-row projection includes it (fully replayable -- set once at
  project creation, carried in the `project.created` event payload).

## [Unreleased] - v4 §8a / P2a: daemon security hardening

Branch `feat/v4-daemon-security`, built on the §1.2/§3 txn-discipline
foundation and the §4a hook fast path below. Implements v4 §8a in full
("the largest defect in v3... the reason v4 exists") and P2a's
acceptance criteria from §11/§10. This is a security-motivated rewrite
of the daemon's auth model, not an additive patch.

### Added
- `core.daemon.SessionManager`: in-memory-only 256-bit session token
  (`secrets.token_urlsafe(32)`), minted fresh on every `SessionManager()`
  construction (i.e. every `serve` restart -- control 6), with an idle
  timeout (`verify_and_touch`, default 8h since last successful use --
  control 6, see docs/decisions.md #85 for the "since last request, not
  since issuance" reading). Replaces the old file-backed
  `create_session`/`verify_session` pair entirely; **v3's
  `~/.muvue/session` file-writing is deleted** (control 5).
- `api/app.py::SecurityMiddleware` (a `@app.middleware("http")`
  function): validates `Host` (control 2, DNS rebinding), validates
  `Origin` before auth (control 3, CSRF -- including non-preflighted
  simple-request CSRF), enforces `Content-Type: application/json` on
  any mutating request that carries a body (control 4), and strips any
  `Access-Control-*` response header as a belt-and-suspenders
  invariant (this app never adds CORS middleware to begin with).
- `POST /auth/exchange`: exchanges the one-time URL-fragment token for
  an `HttpOnly`, `SameSite=Strict` `muvue_session` cookie (control 5).
  `static/index.html`'s dashboard JS now performs this exchange
  automatically on load (reading `location.hash`, then scrubbing it via
  `history.replaceState`) instead of persisting a pasted token to
  `localStorage` (removed -- an XSS-readable, disk-backed token store).
- `cli/main.py::serve`: `--i-know-this-is-exposed` flag gating any
  non-loopback `--host` (control 1); prints the dashboard URL with the
  one-time `#fragment` instead of a bare token line; binds and
  `listen()`s its own socket before printing the readiness line and
  hands the fd to uvicorn (fixes a pre-existing race between that line
  and the socket actually accepting connections -- see
  docs/decisions.md #87).
- **Every mutating API endpoint, agent verbs included, is now
  session-token-gated** (control 4) -- a deliberate widening from
  P2/P2b's "human verbs only" design; see docs/decisions.md #84 for why
  `start` (the plan's own named RCE surface) could not stay
  unauthenticated.
- `core.doctor.run_security_probes` + `run_doctor`'s new
  `skip_security_probes`/`daemon_port` params (control 7): live HTTP
  probes (bad `Host`, bad `Origin`, form-encoded POST, missing/
  query-string token) against a running daemon, spinning up a
  throwaway one against an isolated scratch repo when nothing is
  already listening (never against the repo being checked -- see
  docs/decisions.md #87). `muvue doctor` gained `--skip-security-probes`
  and `--daemon-port`.
- `docs/threat-model.md`: real content (was a placeholder stub) --
  in-scope attack surface, all seven controls and what each buys vs.
  doesn't, and the explicitly out-of-scope same-user/ptrace/MCP-
  prompt-injection risks, per v4 §13.
- `tests/test_daemon_security.py`: the five required §10 tests (bad
  `Host`, bad `Origin`, form-encoded POST, token-in-query-string,
  missing token), each run against a real `muvue serve` subprocess,
  each asserting both a `403` and that the targeted node's DB state is
  provably unchanged.
- `tests/test_doctor_security_probes.py`: control 7 coverage, including
  "a security probe never mutates the repo it's checking" and the
  already-running-vs-throwaway-daemon branches.
- `tests/test_no_token_touches_disk.py`: a full serve -> exchange ->
  approve flow, asserting no new file appears under `.muvue/` and
  nothing session/token-shaped exists on disk afterward.

### Changed
- `api/app.py`: every route handler that used to accept
  `authorization: str | None = Header(...)` now takes `request: Request`
  and calls a shared `_require_session(request)` (checks the
  `Authorization` header, then the `muvue_session` cookie); rejection
  status changed from `401` to `403` throughout, matching v4 §10's
  acceptance text verbatim ("each must 403").
- `tests/test_api.py`, `tests/test_api_p6.py`, `tests/test_api_p7.py`:
  `TestClient` now uses `base_url="http://127.0.0.1"` (httpx's
  `http://testserver` default fails the new `Host` check); tests that
  exercise previously-unauthenticated agent-verb endpoints now pass a
  valid `Authorization` header, pulled from the app's own
  `app.state.session.token` rather than the removed
  `daemon.create_session(repo)`.
- `tests/test_daemon.py`: session-token tests rewritten against
  `SessionManager` directly (round-trip, wrong token, idle timeout,
  rotation-on-restart) -- the old file-based
  `create_session`/`verify_session` round-trip/expiry/rejection tests
  no longer apply to a mechanism that no longer touches disk.
- `tests/test_doctor_queue_depth.py`, `tests/test_cli_drain_callback.py`,
  `tests/test_adapters.py`, `tests/test_strict_mode.py`: pass
  `skip_security_probes=True`/`--skip-security-probes` to stay decoupled
  from the new (and, for a throwaway daemon, ~1-2s slower) live-probe
  pass, which is orthogonal to what those tests assert.

### Removed
- `core.daemon.create_session`, `core.daemon.verify_session`,
  `core.daemon.session_path`, `SESSION_RELPATH` -- the entire
  file-backed session mechanism. `.muvue/session` is still listed in
  the `.gitignore` block `repo_init` writes (harmless, defensive; kept
  so a pre-v4 install's leftover file is still ignored) but nothing in
  this codebase writes to that path anymore.

## [Unreleased] - v4 §4a / P0.5: hook fast path

Branch `feat/v4-p0.5-hook-fast-path`, built on the §1.2/§3 foundational
slice below. Implements the phase described in v4 §4a and the P0.5 row
of §11: a stdlib-only, per-tool-call-safe hook entry point, replacing
the full Typer CLI (~150-400ms cold, measured on this machine) as the
target of every installed git/Claude-Code hook shim.

### Added
- `src/muvue/_hook.py`: new top-level module (sibling to
  `muvue/__init__.py`, not inside `core/`). Imports **only** `sys`,
  `os`, `json`, `time` at module scope; `sqlite3` lazily, only inside
  `PreToolUse`'s DB-reading branch. Never imports `muvue.core` or
  anything from `muvue/__init__.py` beyond the bare package init.
  Proven by a subprocess-based import-graph test
  (`tests/test_hook_import_graph.py`), not a grep of its own imports.
- Default action for every hook event except `PreToolUse`: append one
  minimal JSON line to `.muvue/queue.jsonl` (gitignored, append-only
  spool) and exit -- no DB open. `post-commit` resolves HEAD's sha by
  reading `.git/HEAD`/refs directly (no `subprocess`, no `git`
  invocation). `PreToolUse` remains the one DB-reading path: read-only
  connection, same decision logic as before, hard 150ms wall-clock
  deadline enforced by an elapsed-time check after the query, fail-open
  plus a spooled `hook_timeout` line on overrun.
- `core.hooks.drain_queue`: bounded queue drain (200 items or 200ms,
  whichever first) reusing the existing full handlers
  (`handle_post_commit_from_git_sha`, a new sha-parameterized sibling of
  `handle_post_commit_from_git`); every other spooled event type is
  recorded as a `hook.<event>` audit event. Wired into (a) a Typer app
  callback in `cli/main.py` that runs before every CLI command
  (best-effort, silent), and (b) the daemon's SSE loop
  (`api/app.py`'s `/events/stream`, drained on a second connection
  distinct from the one polling `PRAGMA data_version`).
- `core.doctor`: reports `.muvue/queue.jsonl`'s line count, warns
  (non-fatal, new `DoctorReport.warnings`) above 1000.
- `core.repo_init.hook_fast_path_command`: the shared shim-command
  builder (`PYTHONPATH=<dir> <abs-python> -S -m muvue._hook NAME`) used
  by both the git-hook shim writer and the Claude Code adapter config
  writer, so both stay in sync.
- Latency benchmark (`tests/test_hook_latency_benchmark.py`): 60 real
  cold-subprocess iterations, asserts p95 < 60ms / p99 < 120ms. Measured
  on this machine (no CI runner available in this environment): p95
  ~20ms, p99 ~24ms.

### Changed
- Every hook shim `init`/`doctor --repair` writes (`.git/hooks/*` or
  `.husky/*`) and `muvue adapter install claude-code`'s
  `.claude/settings.json` entries now invoke `muvue._hook NAME` instead
  of the full `muvue hook NAME` CLI. `muvue hook NAME [PATH]` itself is
  unchanged and still directly callable. `core.doctor`'s absolute-path
  shim check and `core.adapters`'s idempotent-reinstall detection
  updated to recognize both the new and the pre-v4 command shape.
- `.gitignore` marker block `init` writes now also ignores
  `.muvue/queue.jsonl`.

### Fixed
- The daemon's SSE loop (`/events/stream`) used one long-lived
  connection for both the `PRAGMA data_version` poll and (this
  session's new) periodic drain; a write committed by a connection is
  not reliably visible in that *same* connection's own next
  `data_version` read, so the drain-triggered write went undetected.
  Fixed by giving the loop a second, dedicated write connection for the
  drain task (caught by `tests/test_sse.py`'s new drain test).

### Behavior change (documented, not a regression -- see
docs/decisions.md #80)
- Claude Code's `SessionStart` hook no longer injects `brief` as
  synchronous `additionalContext`, and `Stop` no longer synchronously
  blocks ending a turn with unlogged `in_progress` work. Both were
  read-only advisory checks (no deferred *write* to lose); v4 §4a's
  "every event except `PreToolUse` spools and exits" applies to them
  literally, so what they used to check is now only visible after the
  fact, as a `hook.<event>` audit event once the queue drains.

## [Unreleased] - v4 §1.2/§3 foundational slice: BEGIN IMMEDIATE transaction discipline, schema deltas, replay-scope redefinition

First phase of the v4 handoff plan migration (branch
`feat/v4-txn-discipline-schema`). Foundational for the follow-up
sessions covering the hook fast path, daemon security, per-driver
budgets, `--parallel` restriction, branch coherence, structure-ref
commits, and trailer-enforcement relocation — none of which this
session touches.

### Changed (BREAKING internally, migrated automatically)
- `SCHEMA_VERSION` 3 -> 4: `projects.closed_at`, `nodes.lease_expiries`
  (split from `attempts`), `events.actor_evidence`; new `agent_spend`
  and `actual_touches` tables. `core.migrate.run_migrate` upgrades an
  existing database in place.
- `core/db.py`: `connect()` now opens with `isolation_level=None`
  (autocommit) so transaction boundaries are fully explicit. New
  `write_txn`/`read_txn` context managers (v4 section 1.2); `write_txn`
  issues `BEGIN IMMEDIATE`, not sqlite3's default deferred `BEGIN` --
  "a deferred transaction that reads first and writes later raises
  `SQLITE_BUSY` immediately on upgrade and is not retried by
  `busy_timeout`." Nesting-safe via `conn.in_transaction`.
- **Every mutating function across every `muvue.core` module** now
  opens its writes inside `write_txn` instead of raw `conn.execute(...)`
  + scattered `conn.commit()` -- `nodes.py`, `projects.py`, `events.py`,
  `gates.py`, `asks.py`, `revisions.py`, `close.py`, `merge.py`,
  `strict.py` (no DB writes of its own -- confirmed, unchanged),
  `drift.py`, `imports.py`, `runner.py`, `drivers.py` (no DB writes --
  confirmed, unchanged), `hooks.py`, `trailers.py` (no DB writes --
  confirmed, unchanged), `daemon.py`. Also fixed one pre-existing
  working-rule-3 violation found by this sweep: `api/app.py`'s
  `POST /events/{id}/ack` wrote raw SQL directly; now calls the new
  `core.events.ack_event`.
- **Bug fix (v4 section 3/8, changed from v3):** `core.daemon.
  reconcile_leases` (a daemon-restart/crash lease reclaim) now
  increments the new `nodes.lease_expiries` counter instead of
  `nodes.attempts`, and never transitions the node to `failed`. v3
  conflated the two, so a daemon restart could burn a node's retries
  toward `max_attempts`. `attempts` now counts *agent* failures only
  (`core.nodes.fail`, and `core.merge`'s conflict-handling
  `block(bump_attempts=True)`).
- `core.rebuild`: replay-scope redefined per v4 section 3. `rebuild`'s
  equality check now covers only the *replayable projection*
  (existence, status, parent/dep edges, criteria+hash, owner,
  attempts, lease_expiries, notes, commits, approvals) and explicitly
  excludes `lease_until` (wall-clock) and `last_retrieved_at`
  (read-side telemetry) from the comparison -- not merely tolerates
  them matching by chance. v3's P0 acceptance ("replay equals live
  DB") was false as written; this is the honest, narrower claim.
  `docs/protocol.md` updated to match.

### Added
- `core/spend.py`: `agent_spend` (one row per project/driver/unit)
  read/write helper (`record_spend`/`get_spend`/`project_spend`),
  wired into `core.runner._record_usage` alongside `node_usage`.
  Budget *enforcement* is explicitly out of scope this session (see
  docs/decisions.md #72).
- `core.hooks.handle_post_commit` now also writes `actual_touches`
  (one row per node/file path from a linked commit) -- raw data for
  the future prediction-vs-actual drift KPI.
- `events.actor_evidence` threaded through every `muvue.core` mutating
  call site: `"tty"` (CLI default), `"mcp"` (`mcp_server.py`),
  `"dashboard_token"` (`api/app.py`), `"subprocess"` (`core.runner`,
  `core.daemon`'s background loop), `"hook"` (`core.hooks`/
  `core.drift`'s hook-driven paths). Purely additive/observational
  this phase -- nothing enforces on it yet (see docs/decisions.md #73/74).
- `tests/test_concurrency.py`: real-thread concurrency test proving
  `write_txn`'s `BEGIN IMMEDIATE` gives zero `SQLITE_BUSY` escapes and
  no lost updates under N concurrent writers, plus a contrast test
  demonstrating the pre-fix deferred-transaction upgrade race really
  does raise `SQLITE_BUSY` immediately.
- `tests/test_schema_v4.py`, `tests/test_spend.py`: new schema/
  migration and `core.spend` coverage.
- `tests/test_rebuild_property.py`: two new tests for the replay-scope
  redefinition (a genuine replayable-field discrepancy is still
  caught; a non-replayable `lease_until` difference is no longer
  flagged).
- `tests/test_daemon.py`: `attempts`/`lease_expiries` split regression
  tests (a lease reclaim, run repeatedly past what would have been
  `max_attempts`, never reaches `failed`; a genuine `nodes.fail` still
  does).
- `docs/threat-model.md`: placeholder stub (v4 working rule 6) --
  real content belongs to the separate §8a daemon-security session.

## [Unreleased] - real GitHub API wiring for `import`/`merge --pr --create`

Post-P8 remediation: fills the network-free stubs P6/P8 documented as
needing future work, now that a real authenticated `gh` CLI is available.

### Added
- `src/muvue/core/github.py`: `fetch_issue_via_gh`/`create_pr_via_gh`,
  real `gh issue view`/`gh pr view`/`gh pr create` subprocess wrappers
  (muvue never touches GitHub credentials directly -- relies on `gh`'s
  own login, same posture as the vendor-agent drivers).
- `muvue import --from github#N` fetches live via `gh` by default now
  (`--data PATH` still available to bypass it); new `--repo owner/name`
  option.
- `muvue merge --pr --create` opens a real PR via `gh pr create`;
  `--pr` alone is unchanged (body text only, no write). New `--repo`
  option.
- `tests/test_github_live_fetch.py`: mocked-subprocess unit coverage
  for both success and failure paths of all three `gh` calls.

### Verified
- Live end-to-end against a real (throwaway, created-and-closed-during-
  verification) GitHub issue: `core.github.fetch_issue_via_gh` and the
  full `muvue project create -> spec -> approve -> decompose -> import`
  CLI loop both fetched and linked the real issue correctly.
- `create_pr_via_gh` verified via its mocked unit tests only, not a live
  throwaway PR (see docs/decisions.md #69 for why).

## [Unreleased] - P8 (thin VS Code extension: webview + command bridge)

Final phase of the handoff plan (plan section 11 row P8).

### Added
- `vscode-extension/`: a thin VS Code extension, `src/extension.ts`
  (113 lines) + `src/lib.ts` (86 lines, no `vscode` import so it's
  unit-testable with plain `node`). Registers three commands
  (`muvue.openDashboard`, `muvue.approveNode`, `muvue.pauseProject`),
  each a real call into the daemon's existing HTTP API (P2's
  `src/muvue/api/app.py`) -- no new backend endpoints, no dashboard
  logic duplicated.
- Webview: `<iframe src="<daemon-url>">`, so the dashboard rendered is
  whatever `GET /` the running `muvue serve` daemon currently serves
  (P2's `index.html`, extended by P7) -- never a bundled copy. SSE
  (`GET /events/stream`) and all other dashboard `fetch()` calls run
  same-origin with the daemon from inside that iframe, unchanged.
- `tests/test_vscode_extension_p8.py`: real-process integration test,
  spawns an actual `muvue serve` subprocess (same pattern as
  `test_serve_integration.py`) and asserts `GET /` is byte-identical to
  the shipped `index.html` and `GET /events/stream` is a live SSE
  endpoint that emits a `data:` line -- the two URLs the extension's
  webview and bridge code depend on.
- `vscode-extension/test/lib.test.ts`: unit tests (plain `node`, no
  framework) for webview HTML generation, command-to-endpoint mapping,
  auth header construction, and URL joining.
- `vscode-extension/README.md`: build/run/test instructions and an
  explicit statement of what was and wasn't verifiable in an
  environment with no live VS Code Extension Host.

### Scope notes (see `docs/decisions.md` #66-68)
- The extension assumes `muvue serve` is already running; it never
  spawns or manages that process.
- No CORS header was added to `src/muvue/api/app.py`: the iframe is
  same-origin with the daemon, and the extension's own `fetch()` calls
  run in the (non-browser) extension host process, so no genuine gap
  was found.
- Not run inside a real VS Code window in this environment -- see the
  README's "What is verified, and what is not" section.

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
