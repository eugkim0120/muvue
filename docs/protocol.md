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
- `export [PATH]` — minimal events dump to `.muvue/history/events.json`
  (full `.jsonl.gz` history export ships P6).
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
- A human answers with `core.asks.answer` (not yet a CLI verb in P1 — no
  human-verb entry point was specified for it; the `dashboard`'s inbox
  ships P2). The answer becomes a `feedback` note on the node
  (`nodes.add_note`).
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
