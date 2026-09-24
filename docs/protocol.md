# muvue protocol (P0 surface)

`protocol_version = 1` (see `.muvue/config.toml`). Bump on any verb change.

All mutating verbs go through `muvue.core` — the CLI never issues raw SQL.

## Node status machine

```
pending -> ready -> in_progress -> review -> done
```

Side states: `awaiting_approval`, `blocked(reason)` where
`reason ∈ {conflict, rate_limit, question, external}`, `failed` (set when
`attempts >= max_attempts`).

Only the lease owner (`nodes.owner`) may perform a transition that the
table below marks as owner-required. See `src/muvue/core/state_machine.py`
(`TRANSITIONS`) for the authoritative, exhaustively-tested table.

| from | to | owner required |
|---|---|---|
| pending | ready | no |
| ready | pending | no |
| ready | in_progress | no (owner is set as part of this edge, by `start`) |
| in_progress | review | yes |
| review | done | yes |
| review | in_progress | yes |
| in_progress | awaiting_approval | yes |
| awaiting_approval | in_progress | yes |
| in_progress | blocked | yes |
| awaiting_approval | blocked | yes |
| blocked | ready | no (lease released on unblock) |
| blocked | in_progress | yes |
| in_progress | failed | yes |
| blocked | failed | yes |
| in_progress | ready | yes (retry after `fail()` below `max_attempts`) |
| done | blocked | no (P5: a merge conflict discovered after `done`) |

## Verbs implemented in P0

### Ops (`muvue <verb>`)
- `init [PATH]` — scaffold `.muvue/` (config, db, hook shims, `.gitignore`).
- `uninit [PATH]` — remove `.muvue/` and precisely reverse every file `init`
  touched.
- `doctor [--repair] [PATH]` — validate config, db schema version, hook
  shim presence/absolute-path correctness; `--repair` reinstalls missing
  shims.
- `migrate [PATH]` — bring `muvue.db` to the current `SCHEMA_VERSION`.
- `rebuild [PATH]` — replay `events` and report any mismatch against the
  live DB.
- `export [PATH]` — minimal events dump to `.muvue/history/events.json`.
  `export --project-id ID [PATH]` (P6, see below) writes the real
  per-project `.muvue/history/<id>.jsonl.gz` archive instead.
- `audit` — stub, ships P7.
- `hook NAME` — shim entry point installed by `init`; no business logic
  yet (ships P3/P4). Must stay cheap (<50ms) per plan section 1.

### Agent verbs (real in P0: `start`, `done`, `fail`)
- `start NODE_ID --owner OWNER [--request-id ID] [--path PATH]` —
  `ready -> in_progress`, sets `owner` + `lease_until`. Duplicate
  `--request-id` within 24h is a no-op.
- `done NODE_ID --owner OWNER [--request-id ID] [--summary TEXT]` —
  drives `in_progress -> review -> done` in one call (P0 simplification,
  see `docs/decisions.md`: criteria evaluation and human review ship P1).
  No-op if the node is already `done`, or on a duplicate `--request-id`
  within 24h.
- `fail NODE_ID --owner OWNER --lesson TEXT [--request-id ID]` —
  increments `attempts`; transitions to `failed` if
  `attempts >= max_attempts`, else back to `ready` for retry. Always
  records a `lesson` note (`trigger`, `failure`, `do_instead`, `scope`).

Stubs (print `not implemented in P0`): `brief`, `show`, `note`, `status`.

### Gates (P1)

- **Gate 1.** Agent writes a spec node (`kind=spec`, `body_md`) via
  `core.gates.submit_spec` -> created `pending`. A human calls
  `approve spec:ID` (`core.gates.approve_spec`) to move it `pending ->
  ready` before decomposition into tasks.
- **Gate 2.** Agent decomposes the spec into task nodes with
  `criteria_json` (`criteria_mode`, `predicted_touches`) -> created
  `pending`. A human calls `approve gate2:PROJECT_ID`
  (`core.gates.approve_gate2`) to approve every currently-pending
  task/subtask node under the project in one call: each node's
  `criteria_json` is hashed into `criteria_hash` (frozen) and the node
  moves `pending -> ready`; the project's `phase` flips
  `planning -> executing`. `start` (`core.nodes.start`) refuses
  `ready -> in_progress` while `project.phase == "planning"`, raising
  `NodeError`.
- **Granularity lint** runs at Gate 2 approval time (`core.gates.lint_task`,
  called from `approve_gate2` for every node it approves — see
  `docs/decisions.md` for why approval time was chosen over decomposition-
  submission time). Warns, never blocks, when a task's `predicted_touches`
  count exceeds `planning.max_files_per_task`, its subtask count exceeds
  `planning.max_subtasks`, or `criteria_mode != "auto"` (P1's reading of
  "lacks an auto criterion" — see `docs/decisions.md`).
- **Criteria edit after freeze.** `core.gates.edit_criteria` on an already-
  frozen node (`criteria_hash` not `NULL`) whose new criteria hash differs
  from the frozen one: bumps `risk_tier` to `high` and, if the node was
  `ready`, demotes it `ready -> pending` (`core.nodes.to_pending`) so
  `start` is refused until a human re-approves it via
  `approve node:ID` (`core.gates.approve_node`, which re-freezes
  `criteria_hash` and moves `pending -> ready`). Editing criteria on a
  node that was never frozen (no prior approval) is a normal edit — no
  re-tier, no re-approval required.

### `ask` / `wait` (P1)

- `ask NODE_ID --question TEXT --default TEXT [--request-id ID]`
  (`core.asks.ask`) creates an open row in `questions` with a proposed
  default answer; recorded as a `question.asked` event. Dedupes on
  `--request-id` like the other agent verbs.
- A human answers with `muvue answer QUESTION_ID --text TEXT` (CLI) or
  `POST /questions/{id}/answer` (API, session-token gated like the other
  human verbs) — both call `core.asks.answer` (human verb, never MCP;
  `core.asks.HumanOnly` refuses a non-human `actor`, same pattern as
  `core.gates.HumanOnly`/`core.nodes.HumanOnly` — see the dogfood-gate
  follow-up in `docs/decisions.md`). Originally shipped with no entry
  point at all in P1/P2; this closed that gap. The answer becomes a
  `feedback` note on the node (`nodes.add_note`).
- `wait QUESTION_ID [--timeout SECONDS] [--default-ok]`
  (CLI polls `core.asks.wait` every second up to `--timeout` wall-clock
  seconds, or once if omitted). `core.asks.wait` itself takes an
  injectable `now`, so its timeout/default-ok semantics are unit-tested
  by manipulating timestamps, not by sleeping for real minutes:
  - Already answered: returns the answer, node is untouched.
  - Before `planning.ask_timeout_minutes` have elapsed since the question
    was asked: returns `{"status": "pending"}`, node is untouched.
  - Past `ask_timeout_minutes`, `--default-ok`: the proposed default is
    applied as a `feedback` note (`question.default_applied` event) and
    the node proceeds (no status change).
  - Past `ask_timeout_minutes`, no `--default-ok`: the question is marked
    `timed_out` and the node moves `in_progress -> blocked(question)`
    (`core.nodes.block`, requires the node's current lease owner).

### Plan revisions (P1)

`plan_revisions(project_id, n, approved_at)` is append-only; approval is
diff-only against the previous revision:

- `propose-revision PROJECT_ID --node-ids ID,ID,...`
  (`core.revisions.propose_revision`) snapshots the given nodes'
  criteria-json hashes into `plan_revision_nodes` under a new revision
  `n = max(n) + 1`.
- `core.revisions.diff_revision(project_id, n)` compares revision `n`'s
  snapshot against `n - 1`'s: `added` (in `n`, not in `n - 1`), `removed`
  (in `n - 1`, not in `n`), `changed` (in both, hash differs), `unchanged`
  (in both, hash equal).
- `approve revision:PROJECT_ID:N` (`core.revisions.approve_revision`)
  re-validates/re-approves only `added` and `changed` nodes (via
  `core.gates.approve_node`: freeze + `pending -> ready`) and soft-deletes
  `removed` nodes. `unchanged` nodes are never read or written — their
  `status` and `criteria_hash` are untouched. Approving the same revision
  twice is a no-op.
- `replan PARENT_TASK_ID --title TEXT` (`core.revisions.replan_add_subtask`)
  adds a subtask directly to `ready` under an already-Gate-2-approved task
  (`criteria_hash` not `NULL`) — no new approval, since it's scoped inside
  the parent's already-approved criteria. Raises `GateError` if the parent
  hasn't been Gate 2 approved yet. New tasks, deletions, and criteria
  changes always go through a plan revision instead.

### Human verbs

Real in P1: `approve` (spec/node/gate2/revision targets above). Stubs:
`reject`, `ack`, `merge`, `close`, `pause`, `resume`, `handoff`, `import`.
Never exposed over MCP.

### Risk tiers (P2)

`core.risk.compute_tier` (plan section 5) is the single source of truth,
used by `core.gates.approve_node` (initial freeze only -- a re-approval
after a criteria edit never recomputes down, see below) and by
`core.nodes.done` when called with `config`:

- `criteria_edited=True` or `has_deletions=True` -> `high` unconditionally.
- Any `predicted_touches` path matches `risk.globs` -> `high`.
- Touch count over `risk.max_diff_lines` -> `high`; over
  `planning.max_files_per_task` -> `medium`; else `low`.
- `core.risk.max_tier(a, b)` merges two tiers, keeping the more severe --
  used so a `done`-time diff-signal recompute can never downgrade a tier a
  criteria edit already forced to `high` (P2 acceptance #4).
- `core.risk.is_flagged` -- true if any predicted touch looks test-shaped.
  "Diffs touching test files or criteria are always flagged" (plan
  section 5): always overrides auto-approval regardless of tier.

### `done -> review` gating (P2)

`core.nodes.done(..., config=None)`: with `config=None` (every P0/P1 call
site), behavior is unchanged -- unconditional `in_progress -> review ->
done`. With `config` given (CLI and API always pass it), the node is
routed through `core.risk`: `low` tier and unflagged auto-approves
straight through to `done` (records a `review.auto_approved` event, actor
`daemon`); otherwise the node stops at `review` (records a
`review.awaiting` event) and needs `core.nodes.approve_review` (human
`review -> done`, logs `metric.rubber_stamp` if under 10s elapsed since
entering `review`) or `core.nodes.reject_review` (`review -> in_progress`
+ a `feedback` note).

Note: `review.auto_approved` / `review.awaiting` deliberately do **not**
start with `node.` -- `rebuild.py` treats any `node.*` event's payload as
a full node-row snapshot; these two are metadata-only payloads
(`{"tier": ..., "flagged": ...}`), so a `node.`-prefixed name would break
replay (`payload["id"]` KeyError). This is exactly the two-times-fixed
replay pitfall the P2 prompt warned about.

### Daemon and API (P2)

`muvue serve [PATH] [--host] [--port]` (`core.daemon` + `muvue.api`):
one daemon per repo, holds no in-memory state (plan section 1). On
startup, before opening the socket: `core.daemon.reconcile_on_start`
reverts every `in_progress` node whose `lease_until` is already past back
to `ready` (`attempts + 1`; `failed` if that exhausts `max_attempts`),
then drains the event queue (`events.acked_at`; P2's consumer is a
documented no-op -- anchor-hashing/staleness are P3+ structure-layer
work). A fresh session token is minted (`core.daemon.create_session`,
written to `<repo>/.muvue/session`) and printed once.

`muvue.api.create_app(repo_root, config)` mirrors the CLI verbs 1:1
(FastAPI, OpenAPI at `/openapi.json` for free):

- Ops/read (unauthenticated): `GET /healthz`, `GET /` (dashboard),
  `GET /events/stream` (SSE, one connection held for the stream's
  lifetime polling `PRAGMA data_version` -- see `docs/decisions.md` for
  why a fresh connection per poll does not work), `GET /inbox`,
  `GET /kpis`, `GET /projects`, `GET /projects/{id}`,
  `GET /projects/{id}/revisions`, `GET /events`, `GET /nodes`,
  `GET /nodes/{id}`, `GET /nodes/{id}/diff` (stub: committed files only,
  no real diff capture until git integration ships), `GET
  /nodes/{id}/logs` (stub: NDJSON of the node's own event history, no
  live agent process until P5's runner).
- Agent verbs (unauthenticated -- "agent verbs stay CLI/local", the API
  exposes them for the dashboard/automation but doesn't gate them):
  `POST /nodes/{id}/start[?agent=X]` (the `agent` query param is recorded
  via a `node.agent_requested` event; nothing is spawned -- P5 scope),
  `POST /nodes/{id}/done`, `POST /nodes/{id}/fail`, `POST
  /nodes/{id}/ask`, `POST /questions/{id}/wait`, `POST
  /nodes/{id}/replan`, `POST /projects/{id}/propose-revision`, `POST
  /nodes/{id}/comment` (spec inline comments, reuses `feedback` notes).
- Human verbs (plan section 4's "never exposed over MCP" -- the API does
  expose them, gated instead by the session token per the P2 prompt):
  `POST /nodes/{id}/approve` (`target ∈ {spec, node, gate2, revision,
  review}`), `POST /nodes/{id}/reject`, `POST /events/{id}/ack`, `POST
  /projects/{id}/pause`, `POST /projects/{id}/resume`, `POST
  /projects/{id}/close`. Stubs: `POST /nodes/{id}/merge`, `POST
  /nodes/{id}/handoff`, `POST /import`. All require `Authorization:
  Bearer <token>` verified against `<repo>/.muvue/session`; missing or
  invalid -> `401`.

`pause`/`resume` are real: `projects.set_phase` to `paused`/`executing`.
`nodes.start` now refuses while `project.phase` is `planning` **or**
`paused` (plan section 5 "Emergency stop... refuses start").

## Leases and optimistic version (P3, ships v0.1)

- `start` refuses a second owner on an already-leased, unexpired
  `in_progress` node with a precise `NodeError` ("leased by X until Y"),
  in addition to the state machine's implicit refusal (no
  `(in_progress, in_progress)` edge). `planning.lease_minutes` (config,
  default 60) sets the lease length; CLI/API `start` read it instead of
  a hardcoded default.
- `nodes.version` (already in the P0 schema) is bumped by
  `add_note(actor="human")` and `core.gates.edit_criteria` (records
  `node.version_bumped`). `done`/`fail` accept `expected_version`
  (`--version` on the CLI): a mismatch raises `VersionMismatch` (a
  `NodeError` subclass) instead of overwriting a human's edit made
  between `start` and `done`. Omitting it preserves prior behavior.
- Expired leases still revert to `ready` (`attempts + 1`) only on
  `core.daemon.reconcile_on_start` (P2) -- unchanged.

## Post-commit hook (P3)

`muvue hook post-commit [PATH]` (invoked by the shim `init` installs)
parses every `Muvue-Node:`/`Refs:` trailer line in `HEAD`'s commit
message (`core.trailers.parse_node_ids`), de-duplicated across *all*
matching lines -- this is what makes it survive a squash-merge commit
that concatenates several original commits' trailers. Every resolvable
node id (exists, not soft-deleted) gets linked into `node_commits`
(`commit.linked` event); unresolvable ids are silently skipped
("trailers are labels, not trusted for binding" -- plan section 5).
Linked commits also enqueue `anchor.hash_requested`/`staleness.flagged`
no-op events (P2's documented no-op-queue-consumer pattern; real anchor
hashing/staleness is structure-layer, P6+). Husky/pre-commit-framework-
aware YAML rewriting is out of scope for P3 (see `docs/decisions.md`);
the shim still installs straight into `.git/hooks/post-commit` (or
`.husky/post-commit`) either way, so the hook runs regardless.

## Human-verb enforcement in core (P3)

`core.gates.approve_spec`/`approve_node`/`approve_gate2`,
`core.revisions.approve_revision`, `core.nodes.approve_review`/
`reject_review` now raise `HumanOnly` (a `GateError`/`NodeError`
subclass) themselves when `actor != "human"`, instead of relying only on
the CLI/API/MCP surface never routing an agent to them.

## MCP stdio server (P3)

`muvue mcp [PATH]`: hand-rolled JSON-RPC 2.0 over stdio (no MCP SDK
dependency), one message per line each direction. Exposes exactly the
agent verbs as MCP tools -- `brief`, `show`, `start`, `done`, `fail`,
`note`, `ask`, `wait`, `replan`, `status` -- never the human verbs (plan
section 4). `tools/call` errors surface as JSON-RPC error objects
(`-32601` unknown tool, `-32602` missing argument, `-32000` a
`core`-raised error), never a raised exception across the wire.

## Real agent-verb implementations (P3)

`brief NODE_ID`, `show NODE_ID`, `note NODE_ID --text TEXT [--kind
KIND] [--pinned]`, `status [--project-id ID]` are real now (`core.
queries`), no longer P0 stubs. `brief` returns the node, its
lesson/pinned notes, and open questions; `show` returns the node, all
notes, linked commits, and predicted touches; `status` returns node
counts by status.

## Claude Code / Codex / Gemini / Cursor adapters (P3)

`muvue adapter install <name>`:

- `claude-code`: merges a `hooks` section into `.claude/settings.json`
  (idempotent -- re-running replaces only muvue's own entries, detected
  by `-m muvue hook` in the command string; preserves unrelated keys and
  other tools' hook entries) for `SessionStart`, `PreToolUse`,
  `PreCompact`, `Stop`, plus a `_muvue.protocol_version` marker.
  `core.doctor` warns if that marker no longer matches the repo's
  current `protocol_version`.
- `codex` / `gemini` / `cursor`: config writers only
  (`AGENTS.md`/`GEMINI.md`/`.cursor/rules/muvue.mdc`), best-effort and
  **unverified** against live vendor docs -- see `docs/providers.md`.

`muvue hook session-start|pre-tool-use|pre-compact|stop [PATH]` (what
the installed Claude Code config invokes): reads hook-specific JSON from
stdin, resolves the active node id from the payload or
`.muvue/current_node` (written by `start`, cleared by `done`/`fail` --
`core.adapters.set_current_node`/`get_current_node`/
`clear_current_node`), and prints a JSON decision (`core.claude_hooks`)
to stdout: `session-start` -> `brief` as `additionalContext`;
`pre-tool-use` -> blocks `Edit`/`Write` with no `in_progress` node or an
`awaiting_approval` one, blocks a `git commit` `Bash` call with no
`Muvue-Node:`/`Refs:` trailer; `pre-compact` -> requires a summary;
`stop` -> blocks ending the turn with an `in_progress` node that has
zero notes logged. A `"decision": "block"` response exits 2 (Claude
Code's documented convention).

## Light-mode `review` dispatch (P3)

On top of P2's risk-tier/test-touch gate, `core.review.dispatch`
(called from `nodes.done` when `config.mode == "light"`) adds a
criteria-mode-specific flag: `manual` always waits for a human;
`external` is always flagged (core can't verify an external criterion
in-process); `auto` runs an injectable `run_checks(cmd, cwd) -> bool` in
the checkout -- omitted (every current CLI/API call), it's a no-op and
`core.risk` alone decides, preserving P0-P2 behavior exactly.
`core.review.default_run_checks` is a real subprocess runner, wired into
the CLI (`done --run-checks`) and API (`POST /nodes/{id}/done
{"run_checks": true}`) as an explicit opt-in (default `false`/omitted
preserves risk-tier-only gating byte-for-byte -- see `docs/decisions.md`
#38 and its dogfood-gate follow-up).

## Strict mode (P4)

`config.mode == "strict"` changes `start` and `done`/review internally;
the verb contract from a caller's perspective (arguments, return shape)
is unchanged from P0-P3, so `protocol_version` is **not** bumped (see
`docs/decisions.md` #44).

- **Airlock.** `~/.muvue/airlocks/<repo-hash>.git` -- a bare repo, one per
  muvue-init'd repo. `<repo-hash>` is the first 16 hex characters of
  sha256 of the repo's resolved absolute path (`docs/decisions.md` #41).
  `core.strict.ensure_airlock` creates it on first use, installs its
  `pre-receive` hook shim, and fetches the calling repo's current `HEAD`
  into the airlock's `refs/heads/main` (a fetch, not a push, so the
  airlock's own protected-`main` rule doesn't apply to muvue's own sync).
- **Worktree per node, bound at `start`.** `core.nodes.start(...,
  config=, repo_root=)`, when `config.mode == "strict"`, calls
  `core.strict.bind_worktree` *before* applying the node's status
  transition: `git worktree add` off the airlock's `main`, on a new
  branch `node-<id>`, under `~/.muvue/worktrees/<repo-hash>/node-<id>`.
  The node's `worktree` column (already in the P0 schema) is set as part
  of the same `_apply_transition` call that records `node.start`, so
  `rebuild` replays it for free (it's already in `rebuild._NODE_COLUMNS`).
  Re-`start`ing an already-bound node (e.g. after a lease-expiry retry)
  reuses the existing worktree rather than recreating it.
- **`worktree_setup`.** Runs once, as a subprocess, in the new worktree
  right after `git worktree add` succeeds, before the node's status
  changes at all. A non-zero exit tears the worktree and its branch back
  down and raises `core.strict.StrictModeError` with the command's
  stdout/stderr attached -- `start` fails, the node stays `ready`, and
  `worktree` stays `NULL` (no half-initialized binding, no orphan
  worktree on disk).
- **`pre-receive` enforcement (P4 acceptance criterion 1).** `muvue hook
  pre-receive`, installed on the airlock, reads git's `<old> <new> <ref>`
  pre-receive protocol lines from stdin (`core.strict.handle_pre_receive`)
  and rejects the whole push (git pre-receive is all-or-nothing) if any
  updated ref is invalid:
  - `refs/heads/main` is always rejected -- main is never a direct push
    target in strict mode.
  - `refs/heads/node-<id>` is rejected unless node `<id>` exists, has a
    non-`NULL` `worktree`, and is `in_progress` or `review`.
  Trailers (`Muvue-Node:`) remain labels, same as light mode
  (`core/trailers.py`) -- this hook never reads them; it trusts only the
  `worktree` binding recorded by `start`, per plan section 5. **This
  check is state-based, not origin-based: it cannot verify which process
  or filesystem path a push actually came from on a single machine.
  Documented guarantee: prevents accidental and lazy bypass, not
  adversarial isolation (plan section 13).**
- **Clean-env `auto` criteria + real-diff flagged test edits (P4
  acceptance criterion 2).** `core.review.dispatch`, for
  `config.mode == "strict"` and a node with a bound worktree: runs
  `auto` criteria's `run_checks` in the worktree (not the caller's
  `cwd`) -- already a fresh git checkout by construction, matching plan
  section 5's "clean worktree" contrast with light mode's "runs in the
  checkout". It also computes a real `git diff --name-only main...HEAD`
  in the worktree (`core.strict.worktree_diff_files`) and flags the node
  to `review` if any touched path looks test-shaped
  (`core.risk.is_test_touch`, the same heuristic light mode's
  `predicted_touches`-based proxy already used). A strict-mode node with
  no bound worktree is unchanged from pre-P4: a no-op.
- **Agent never holds a `main` checkout.** Architecturally guaranteed by
  `bind_worktree` always allocating a path under
  `~/.muvue/worktrees/...`, never `repo_root`; `doctor` additionally
  checks (in strict mode) that every `in_progress`/`review` node has a
  bound worktree that still exists on disk and isn't `repo_root` itself.
- **`init --sandbox`.** Additionally writes
  `.muvue/sandbox-compose.yml`, a documented, unimplemented-runtime
  compose scaffold (plan section 5: "(later)"). muvue does not build,
  start, or manage it -- scaffold only, no container orchestration.

## Planning verbs on the CLI (dogfood-gate follow-up)

The P0-P3 planning surface (project creation, Gate 1 spec submission, Gate
2 task decomposition) previously existed only as raw `core` Python calls
(`core.projects.create_project`, `core.gates.submit_spec`,
`core.nodes.create_node`) -- there was no CLI entry point, so anyone
actually driving muvue had to import `muvue.core` directly instead of
going through the protocol surface plan section 4 defines. Closed here
(see `docs/decisions.md` #39):

- `project create --goal TEXT [--budget-unit UNIT] [--budget-limit N]
  [--path PATH]` (`core.projects.create_project`) -- creates a project,
  prints the created row (`id`, `goal`, `phase`, `budget_unit`,
  `budget_limit`).
- `spec PROJECT_ID --title TEXT --body TEXT [--path PATH]` (agent verb,
  `core.gates.submit_spec`) -- Gate 1: creates a `pending` spec node. A
  human then calls `approve spec:ID`.
- `decompose SPEC_ID --title TEXT [--body TEXT] [--criteria TEXT ...]
  [--predicted-touches GLOB ...] [--criteria-mode auto|external|manual]
  [--path PATH]` (agent verb, `core.nodes.create_node`) -- Gate 2: creates
  a `pending` task node under `spec_id` (`parent_id`, `kind=task`). A
  human then calls `approve gate2:PROJECT_ID`.
- `approve review:ID` (`core.nodes.approve_review`) -- the API already
  dispatched this target (`docs/protocol.md`'s Daemon/API section lists
  `review` among `approve`'s targets), but the CLI's `approve` command had
  no `review` branch and no test exercising it through the CLI. Added the
  branch and documented it in `--help`.

`--criteria` and `--predicted-touches` are repeatable options (Typer
`list[str]`), matching how `propose-revision --node-ids` already takes a
comma-separated list elsewhere in this CLI -- repeatable flags were
chosen instead to match `--criteria`'s natural multi-value shape (a task
usually has more than one acceptance criterion) without forcing the
caller to hand-join a comma string.

## `replan` (P1, confirmed complete in P3)

`replan PARENT_TASK_ID --title TEXT [--body TEXT]`
(`core.revisions.replan_add_subtask`) and `POST /nodes/{id}/replan` were
both already real as of P1; P3 found no gap to close here.

## Idempotency

`--request-id` dedupe window: 24 hours, scoped per (verb, request_id).
Notes dedupe by SHA-256 content hash, independent of request-id.

## Events

`events` is append-only and is the source of truth: every mutation in
`muvue.core.nodes` / `muvue.core.projects` inserts a row whose `payload`
is a full JSON snapshot of the row after the mutation. `rebuild` folds the
event stream (last-write-wins per `(table, id)`) and must equal the live
`projects`/`nodes` tables — this is asserted by property-style tests in
`tests/test_rebuild_property.py`.

## Runner, drivers, merge, handoff (P5)

### `muvue run [--agent X] [--parallel N] [--project-id ID]`

Unattended runner (`core.runner.run`), scoped to `task`/`subtask` nodes
(a `ready` `spec` node means "ready for decomposition" — a distinct
workflow this runner doesn't drive, see `docs/decisions.md`).

- At the top of every run: reverts expired leases
  (`core.daemon.reconcile_leases`) and un-blocks any
  `blocked(rate_limit)` node whose recorded `retry_at` has passed
  (`core.runner.reconcile_rate_limits`) — the same reconcile-on-start
  pattern `muvue serve` already uses (plan section 5), extended here.
- Each cycle: if any node in the run's scope is already
  `awaiting_approval`/`blocked`/`failed`, the run pauses immediately
  without scheduling anything new (`{"paused": {"reason":
  "nodes_need_attention", ...}}`) — the simplest deterministic reading of
  "pauses ... when it hits a node in that state" (see
  `docs/decisions.md`). Otherwise: checks the budget
  (`core.runner.budget_state`, reading `config.budget.unit`/`limit`
  against `node_usage`) — exhausted (spend >= limit) stops the run
  (`{"reason": "budget_exhausted"}`); >= 80% logs one
  `runner.budget_warning` event, doesn't stop scheduling.
- Selects a batch of ready nodes via `core.runner.select_batch`: routes
  each by `[routing]` (or `--agent` override), skips a node whose routed
  agent isn't configured, and greedily picks up to `--parallel N` nodes
  whose `predicted_touches` don't overlap any other selected node's,
  never exceeding any one agent's own `max_concurrency` within the batch.
  `--parallel 1` (default) processes nodes serially; `> 1` runs the batch
  concurrently via a thread pool, each thread opening its own SQLite
  connection (no shared connection across threads).
- For each selected node (`core.runner.run_node`): `start` (binds a
  strict-mode worktree if configured), `brief` piped to the driver
  (`core.drivers.invoke_driver`) as a fresh subprocess, then `done`/
  `fail`/`block(rate_limit)` based on what the driver reports. A node
  that lands in `failed`, stays `review` (flagged, not auto-approved),
  or `blocked` after this cycle's batch pauses the run for the *next*
  cycle (existing in-flight work in the same batch still finishes).

### Drivers (`config.agents.<name>`)

Real invocation, `core.drivers.invoke_driver`:

1. If `auth_check` is set, run it first. A non-zero exit ->
   `status="unavailable"` (never a crash) — the runner then applies the
   agent's own `on_rate_limit` `fallback:<agent>` policy if configured
   (reused for driver-unavailable too — plan defines no separate
   `on_unavailable` key), else `block(external)`.
2. Spawn `command` (`shell=True`, `brief` JSON on stdin), capture
   stdout/stderr.
3. Parse usage via `usage_parser`:
   - `claude_stream_json` / `codex_json` / `gemini_json` — **synthetic,
     unverified against a real vendor CLI** (no network access / logged-
     in CLI in this environment — see `docs/providers.md` and
     `tests/fixtures/vendor_samples/`).
   - `fake` — real, tested against a real `muvue-fake-agent` subprocess.
4. A rate-limit signal (vendor-specific error text containing a
   `rate limit`/`429`-shaped marker, or `muvue-fake-agent`'s own scripted
   `status: "rate_limited"`) -> the node goes `blocked(rate_limit)` with
   a `retry_at` (a `runner.rate_limited` event; a non-`node.`-prefixed
   type, like `review.auto_approved`, so `rebuild` doesn't misparse it as
   a row snapshot), and `on_rate_limit` (`wait`/`fallback:<agent>`/
   `pause`) is applied.

Never reads, stores, or passes a vendor credential (plan principle 7):
only shells out and inherits the caller's own environment.

### `muvue-fake-agent`

A real, invocable console script (`src/muvue/fake_agent.py`), distinct
from P3's `tests/fake_agent.py` in-process test fixture. Reads a brief
off stdin, prints JSON lines, scripted via `--behavior`/
`MUVUE_FAKE_BEHAVIOR`: `cooperative`, `lazy`, `adversarial` (noisy/
malformed extra output around a real result line), `rate_limited`,
`crash` (non-zero exit, no result line), `failed`. `--check`/
`MUVUE_FAKE_AUTH_FAIL` simulates `auth_check` (a logged-out CLI).

### `muvue merge [NODE_ID]`

`core.merge.attempt_merge`/`merge_pending` (plan section 6 "Merging").
Strict-mode only (light-mode / never-strict-started nodes: `{"status":
"no_worktree"}`, a documented no-op). Merges a `done` node's branch onto
the airlock's `main`, respecting `deps` order (`deferred` if a dependency
hasn't merged yet). On conflict: `node -> blocked(conflict)`,
`attempts + 1`, a `kind=subtask` "rebase onto main" node created under
it, `status=ready`. Never touches `repo_root`'s own checkout or a remote
— propagating the airlock's `main` back out is out of P5 scope (`merge
--pr` is P6).

### `muvue handoff NODE_ID --to OWNER`

`core.nodes.handoff` (plan section 6 "Handoff"). Human verb, never
exposed over MCP. Reassigns `owner`/`lease_until` on an `in_progress` or
`blocked` node (un-blocking it back to `in_progress` first if needed) so
a different driver can resume purely from DB state — no in-memory
handoff (plan principle 1).

## Close, structure layer, history archive, import, PR body (P6)

### `muvue close PROJECT_ID [--yes]`

`core.close.close_project` (plan section 9). Human verb, never exposed
over MCP. Without `--yes`: a dry-run preview (also `GET
/projects/{id}/close-preview`) of the proposed structure diff, no
mutation. **Closeable gate**: every one of the project's live
(non-soft-deleted) `task`/`subtask` nodes must be `done` (`spec` nodes
are excluded — see docs/decisions.md); otherwise `preview_close`
reports `"closeable": false` and the offending node ids, and
`close_project(..., confirm=True)` raises `CloseError`.

The proposed diff, **capped at `core.close.MAX_DIFF_ITEMS` (20) per
category**:
- `decisions` — one candidate per `kind='decision'` note on the
  project's nodes (title = first line of the note text, choice = the
  full text).
- `promoted_lessons` — one candidate per `kind='lesson'` note with
  `pinned=1` (plan section 9: "a lesson note with `pinned=true` ... are
  'promoted lessons'"), parsed from `fail`'s lesson JSON
  (trigger/failure/do_instead/scope) into the same title/context/choice
  shape as a decision. Promoted lessons land in the `decisions` table
  too (no separate structure-layer table for them).
- `components` — one candidate per distinct `predicted_touches.path_glob`
  the project's nodes declared, not already tracked as a component
  (there is no static-analysis/anchor-hashing scan yet — that ships with
  P7's `audit`).

On `--yes`/`confirm=True`: inserts the diff's rows into `components`/
`decisions` (each recording a `component.created`/`decision.created`
event), dumps the **full current** `components`/`decisions` tables to
`.muvue/components.json`/`.muvue/decisions.json`, commits those two
files on the repo's checked-out `main` (muvue is "the single writer" of
these files — a plain `git add`/`git commit` on `repo_root`, muvue
committer identity, same pattern as `core.merge`'s `_run_git_as_muvue`;
not a strict-mode airlock merge — there is no per-project git branch for
structure metadata), sets `projects.phase = 'closed'`, and exports the
project's event history (below). Also `POST /projects/{id}/close`
(session-token-gated).

### History archive: `.muvue/history/<project-id>.jsonl.gz`

`core.history.export_project`/`rebuild_from_archive` (plan section 2
file layout, section 9). The real per-project archive format — P0's
`export [PATH]` (whole-DB flat `events.json` dump) still exists
unchanged for that case; `export --project-id ID [PATH]` (and `close`,
which calls this internally) now write the real format: gzip-compressed,
one JSON event per line, every event with that `project_id`, in `id`
order.

`core.rebuild.rebuild_state_from_events` is the single fold
implementation both `rebuild_state(conn)` (the whole live `events`
table) and `history.rebuild_from_archive(path)` (one project's exported
archive) call — a project-scoped replay reproduces exactly the same
`{"projects": {...}, "nodes": {...}}` shape as the whole-DB replay,
scoped down to what that archive actually contains.
`muvue rebuild --project-id ID [PATH] [--from-archive PATH]` /
`core.rebuild.diff_project_from_archive` compares that replay against
the project's live state (filtered to its own project id and nodes) —
`{}` means the archive reproduces it exactly.

### `brief` reads structure (plan section 4)

`core.queries.brief_node` now also returns `relevant_decisions`/
`relevant_components`: the top `STRUCTURE_SEARCH_LIMIT` (3) FTS5 hits
against `decisions_fts`/`components_fts` (new virtual tables,
`SCHEMA_VERSION = 2`) for a query built from the node's own
title/body_md, ranked by `bm25()`, `status = 'current'` only. This is
what lets a brand-new project's `brief` cite a decision `close`d on a
completely different, already-`closed` project (P6 acceptance #1) — the
FTS5 tables are global, not scoped to a project.
`core.queries.search_decisions`/`search_components` are the reusable
query functions (also used by `core.pr.generate_pr_body`, below).

### `muvue import --from github#N --node-id ID [--data PATH]`

`core.imports.import_github_issue` (plan section 4/11). Human verb,
never exposed over MCP. No live GitHub API access in this environment
(plan working rule 2, no new HTTP-client dependency) — issue data comes
from one of `data` (inline dict), `data_path`/`--data` (a local JSON
file, `{"number", "title", "url", "body"}`), or an injected
`fetch_fn(issue_number) -> dict` (the seam a real GitHub-API
implementation plugs into later; not built here). Inserts one
`external_refs` row (`system='github'`) and records
`external_ref.added`. Also `POST /import` (session-token-gated; `data`
is a required JSON body field, no file-path option over HTTP).

### `muvue merge NODE_ID --pr`

`core.pr.generate_pr_body` (plan section 4/6/11). No real `gh pr create`
call (no network access here) — returns a markdown PR description body
(`result["pr_body"]`) built from the node's own title/body_md,
acceptance criteria (`criteria_json`), its notes, any relevant decisions
(same FTS5 search `brief` uses), and any linked `external_refs`.
Independent of the merge outcome itself (works the same in light mode,
where `attempt_merge` returns `"no_worktree"`). Also `POST
/nodes/{id}/merge?pr=true` (session-token-gated).

## Drift loop, `audit`, lesson decay, real `drift_pct`/timeline (P7)

`core.drift` (plan section 9) is the first phase to actually write
`components.anchors_json` -- a `{file_path: content_hash}` dict (P6 left
it at the schema default `'[]'`). `core.drift.create_anchored_component`
creates a component anchored to a real file at HEAD, hashing its content
via `git show <sha>:<path>` (sha256 of the blob, not git's own object
hash, so no git-internals dependency in the comparison logic).
Symbol-level/tree-sitter anchors stay explicitly deferred (plan section
9), same as P6.

### 1. Post-commit staleness marking (real, not a no-op signal)

`muvue hook post-commit`'s CLI entry point
(`core.hooks.handle_post_commit_from_git`) now also calls
`core.drift.mark_stale_for_commit(conn, repo_root, commit_sha, files)`
after its existing trailer-linking/no-op-signal work: for every
non-deprecated component whose `anchors_json` names a file this commit
touched, it recomputes the blob hash. A pure rename (detected via `git
diff-tree -M`, real git rename-detection semantics) retargets the
anchor to the new path without marking the component stale; any other
content change flips `components.status` to `'stale'` and records a
`component.updated` event carrying the full row (replayable, same
last-write-wins convention `node.*`/`project.*` events already use --
see `core.rebuild`). `core.hooks.handle_post_commit` itself (the
pure-data function, no git access, used directly by unit tests) is
unchanged; the real git-blob-hashing work lives only in the
`_from_git` entry point, which is what the installed shim actually
calls. The P3-era `anchor.hash_requested`/`staleness.flagged` no-op
events are still recorded (nothing removed), now alongside the real
work they used to just announce.

### 2. Unattributed commits touching an anchor -> inbox

`core.drift.flag_unattributed_commit` (called from
`core.hooks.handle_post_commit`, DB-only, no git access needed): a
commit with no `Muvue-Node:`/`Refs:` trailer at all whose touched files
overlap any tracked component's anchor paths records an unacked
`inbox.unattributed_commit` event. `GET /inbox` surfaces every unacked
one of these under `"signals"`.

### 3. Reconcile-on-touch

`core.review.dispatch` now checks, before its existing light/strict-mode
branching, whether the node's `predicted_touches` globs overlap any
`stale` component's anchor paths (`node_touches`, the real per-node
structure-graph join table, has no populated writer yet anywhere in the
codebase, so `predicted_touches` is the overlap signal used, as the P7
scope itself allows). A hit always flags to `review`
(`review.stale_component_touched`), overriding whatever
tier/criteria-mode would otherwise have auto-approved `done`.

### 4. `muvue audit [--n N]` (real, replacing the P0 stub)

`core.drift.run_audit`: samples the `n` (default
`core.drift.DEFAULT_AUDIT_SAMPLE`, 5) oldest-verified (or
never-verified) non-deprecated components -- `components` carries no
creation timestamp, so "oldest" is read as never-verified first, then
ascending `id` as a proxy for insertion order -- and drafts a proposed
diff for each into the inbox as an unacked `inbox.audit_drift_signal`
event (`GET /inbox`'s `"audit_items"`). Also runs a lesson-decay pass
(below) as part of the same run -- "`audit` may prune" (plan section 9).

### 5. `drift_pct` KPI (real, replacing the P2/P6 0.0 stub)

`core.drift.drift_pct(conn, repo_root, k=DEFAULT_DRIFT_WINDOW_COMMITS)`:
"% components verified within last K commits" (plan section 9), read
literally -- the fraction of non-deprecated components whose
`verified_sha` is among `HEAD`'s last `k` (default 50) commits. 0.0 with
no non-deprecated components, or a repo with no usable git history.
Wired into `GET /kpis`.

### Lesson decay

`core.drift.record_lesson_retrieval` is called from
`core.queries.brief_node` every time a lesson note is surfaced,
recording a `note.retrieved` event (`{note_id, project_id}`) and
bumping `notes.last_retrieved_at`. `core.drift.decay_lessons(k=
DEFAULT_LESSON_DECAY_K)` (default 3) archives any `kind='lesson'`,
unpinned note whose `note.retrieved` events span fewer than `k`
distinct `project_id`s: sets `notes.archived_at` (P7's `SCHEMA_VERSION
3` addition, a dedicated soft-delete column rather than overloading
`pinned`/`last_retrieved_at`) and records a `note.archived` event.
Archived, unpinned lessons are excluded from what `brief_node` surfaces.
Pinned lessons are never decayed regardless of retrieval count.

### Timeline scrubber

`GET /events` gained `since`/`until` query params (`events.ts` window,
inclusive, independent of the pre-existing `since_id` row-id cursor).
The dashboard's timeline tab (`static/index.html`) fetches its usual
100-event window once, then scrubs it client-side with a range slider
(`<input type=range>`, `addEventListener`, no inline handlers, no CDN)
over "oldest event shown" rather than re-fetching per drag.
