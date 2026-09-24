# P0 decisions (plain notes, not the `decisions` table)

Per working rule 6: ambiguous spec items get the simplest reasonable
reading here. Real `decisions` table entries start once dogfooding begins
(P3+).

1. **`done` drives `in_progress -> review -> done` in one call.** Gate
   machinery (auto/external/manual criteria evaluation, human review) is
   P1 scope. Until it exists there is nothing to pause on at `review`, so
   P0's `nodes.done()` performs both edges atomically inside one
   transaction and records both `node.review` and `node.done` events.

2. **Owner is released on `fail()` regardless of outcome.** The spec says
   `failed` triggers at `attempts >= max_attempts` but doesn't say what
   happens to the lease below that threshold. Chose: always clear
   `owner`/`lease_until` and route back to `ready` (so any agent, including
   the same one, can pick it up again) rather than re-`in_progress` on the
   same owner. Added the edge `in_progress -> ready` (owner-required, since
   only the current lease holder can relinquish it) to the state machine
   for this.

3. **`events.actor` is a role, `nodes.owner`/lease identity is separate.**
   The schema's `events.actor` CHECK constrains to
   `{agent, human, hook, daemon}` (plan section 3), while lease ownership
   (plan section 3/5: "only the lease owner may transition a node") is an
   arbitrary identity string (e.g. `"claude-1"`, `"alice"`). `core.nodes`
   keeps these as two separate parameters (`lease_actor` vs
   `event_actor_role`) so both constraints are satisfiable simultaneously.

4. **Hook shims target `post-commit` and `pre-push` only for P0.** Plan
   section 7 lists more adapter-specific hooks (SessionStart, PreToolUse,
   etc.) but those are Claude Code adapter concerns for P3. P0 only needs
   the generic git-level shim file format described in section 5, so it
   installs the two hooks that section 3/7 anchor to git itself
   (post-commit event enqueue, pre-push strict-mode check).

5. **Hook/`.gitignore` reversal uses a full-content backup manifest**
   (`.muvue/.init_manifest.json`: original content or `None` per touched
   file) rather than trying to regex out an appended marker block. This
   guarantees byte-exact reversal (needed for the `git status` clean
   acceptance check) instead of relying on marker-stripping being
   lossless.

6. **`export` in P0 is a flat JSON dump** of the whole `events` table to
   `.muvue/history/events.json`, not the `.jsonl.gz` per-project archive
   described in plan section 9 (that's a P6 `close` concern). It exists
   only so `export` is a real command rather than a bare stub.

7. **`migrate` is idempotent schema-apply + version bump.** P0 only ships
   `schema_version = 1`, so there are no real migration steps yet; the
   command re-runs `CREATE TABLE IF NOT EXISTS ...` and records the
   version. Real migrations arrive when `schema_version` needs to move.

8. **FTS5 is wired for `notes` only in P0** (triggers keep `notes_fts` in
   sync). Brief-ranking (spec section 4: FTS5 over notes, decisions,
   component purposes) is P1+ once `brief` is implemented for real.

## P1 decisions

9. **`criteria_hash` is `NULL` until Gate 2 freezes it, not set at node
   creation.** P0's `create_node` computed `criteria_hash` from
   `criteria_json` unconditionally at insert time. P1 needs a way to tell
   "this node's criteria have been approved at least once" from "this node
   was just created and nobody has looked at its criteria yet" —
   `edit_criteria`'s re-tier logic depends on that distinction (editing an
   unapproved node's criteria is a normal edit; editing an *approved*
   node's criteria re-tiers to `high` and demotes it for re-approval).
   Changed `create_node` to leave `criteria_hash` `NULL`; `core.gates.
   approve_node` is now the only place that sets it, and its presence
   means "frozen as of this hash." No P0 test asserted a value for
   `criteria_hash` at creation, so this is a compatible extension of P0's
   contract, not a break.

10. **Gate 2 approval is project-wide, not per-node.** The plan describes
    Gate 2 as "human approves" the whole decomposition. Implemented
    `approve_gate2(project_id)` to approve every currently-`pending`
    task/subtask node under the project in one call (freeze + lint each,
    then flip `project.phase`), rather than requiring N separate
    `approve node:ID` calls before `phase` can flip. `approve_node` still
    exists standalone for the single-node re-approval path (criteria-edit
    re-tier, plan-revision approval) where a project-wide freeze doesn't
    make sense.

11. **Granularity lint runs at Gate 2 approval time, not at decomposition-
    submission time.** The plan says "pick the simpler reasonable point."
    Approval time means the lint always sees the final state of
    `predicted_touches` and subtask count (an agent may still be adding
    subtasks up until the human looks at it), and it's the same call site
    already iterating every task node, so no second read/aggregation pass
    is needed.

12. **"Lacks an auto criterion" reads as `criteria_mode != "auto"`.** The
    P0 schema (plan section 3) stores one `criteria_mode` enum per node,
    not a mode per individual criterion string in `criteria_json` (a list
    of plain strings). The full per-criterion auto/external/manual split
    described in plan section 5 ("`done` -> `review`... `auto` criteria...
    `external` criteria... `manual` waits") is P2 `review`-machinery scope.
    For P1's lint, "lacks an auto criterion" is read at the node-mode
    granularity: a task whose `criteria_mode` isn't `"auto"` has no
    auto-checkable criterion at all, and warns.

13. **`ask`/`questions` is a new lightweight table, not events-only.** The
    spec allowed either "an `events` entry of type `question` plus a
    `notes` row, or a lightweight table if that's clearly simpler." A
    `questions` table with `status ∈ {open, answered, timed_out}` makes
    `wait`'s three outcomes (still pending / answered / timed out) a
    single indexed row read instead of a scan-and-fold over `events`, and
    keeps `ask`/`answer`/`wait` symmetric with `nodes.py`'s pattern (one
    table, one module, events recorded alongside every mutation). The
    question's answer is *also* recorded as a `feedback` note on the node
    per the plan's explicit instruction ("Answer becomes a `feedback`
    note").

14. **`core.asks.wait` takes an injectable `now`, and the CLI `wait` verb
    is a thin polling loop around it.** Required by the P1 prompt directly
    ("write it so a test can simulate 'past ask_timeout'... not by
    literally sleeping"). The core function is pure with respect to time;
    the CLI's `--timeout SECONDS` is a real wall-clock polling budget for
    interactive use, unrelated to `planning.ask_timeout_minutes` (which is
    compared against the question's `created_at`, not against how long the
    CLI command has been running).

15. **Editing criteria is refused outside `ready`/`pending`.**
    `core.gates.edit_criteria` raises `GateError` if the node is
    `in_progress` or any other status. The plan doesn't specify behavior
    for editing criteria on an in-flight node; the simplest safe reading
    is that criteria edits are a pre-start (planning-time) operation only
    in P1 — mid-execution criteria changes would need to interact with an
    active lease and the not-yet-built `review` gate (P2), which is out of
    scope here.

16. **Plan-revision `removed` nodes are soft-deleted on approval, and
    count as "touched."** The plan states approval "only re-validates/
    re-approves nodes that changed" and that untouched nodes keep their
    state; it doesn't say what happens to nodes dropped from the plan.
    Soft-deleting them (`nodes.soft_delete`, already P0's only deletion
    path) on `approve_revision` is the simplest reading that doesn't leave
    a proposed-for-removal node silently `ready` and startable forever.
    They're included in `touched_node_ids` since they are, by definition,
    part of the diff — "untouched" in the P1 acceptance criterion refers
    to nodes absent from the diff entirely.

17. **`replan_add_subtask` requires the parent task to already be Gate 2
    approved (`criteria_hash` not `NULL`).** The plan says replan "may add
    subtasks within an approved task's stated scope without new approval."
    Read literally: if the task isn't approved yet, there's no "stated
    scope" to add within, so `replan` raises `GateError` and the caller
    must go through Gate 2 / a plan revision first.

18. **Diff size is approximated by `predicted_touches` count, against
    both size thresholds.** Plan section 5 lists "diff size" as a risk
    input, but neither P0 nor P1 track added/removed line counts anywhere
    (`predicted_touches` is a set of path globs; `node_commits.files` is a
    JSON list of paths, once committed -- neither carries a line count).
    Per the P2 prompt's explicit steer ("diff size (from predicted_touches
    count or actual node_commits diff if available -- predicted_touches is
    what P0/P1 already track, use that)"), `core.risk.compute_tier` reads
    touch *count* against `planning.max_files_per_task` (-> `medium`) and
    `risk.max_diff_lines` (-> `high`), even though the latter's name
    implies a line count. This is a proxy, not a real diff-size
    measurement; a real one needs git integration that doesn't exist
    before P3's hooks/structure layer.

19. **`has_deletions` is a wired parameter with no producer yet.** Plan
    section 5 lists "deletions" as a risk-tier input. No P0/P1/P2 table
    records which files a node's diff deleted (`node_commits.files` is
    just a path list, no per-path status). Rather than fabricate a
    deletion-detection mechanism (real git-diff capture is P3+ hook/
    structure-layer scope, explicitly out of P2's bounds), `compute_tier`
    accepts `has_deletions` as an explicit keyword defaulting to `False`
    that nothing currently sets `True`. Documented here rather than
    silently dropped, matching how the P2 prompt allows KPI fields like
    `drift_pct`/`tokens_per_node` to be "stubbed/zero" pending their real
    producers.

20. **Gate 2's initial freeze computes risk tier via `core.risk`; a
    re-approval after a criteria edit does not recompute it.**
    `core.gates.approve_node` only calls `risk.compute_tier` when
    `node["criteria_hash"] is None` (i.e. this is the node's first-ever
    freeze). On a re-approval (the node was frozen once before --
    `edit_criteria` pulled it back to `pending` and bumped `risk_tier` to
    `high`), `approve_node` leaves `risk_tier` untouched. This preserves
    P1's existing, already-tested guarantee
    (`tests/test_gates.py::test_criteria_edit_after_freeze_retiers_and_blocks_start`)
    that `risk_tier` stays `high` through re-approval, and is required by
    P2 acceptance #4: recomputing from diff signals on every approval
    would let an unrelated, small diff silently downgrade a
    criteria-edit-forced `high` back to `low`.

21. **`core.nodes.done`'s tier-gated review path only activates when
    `config` is passed; `config=None` preserves P0/P1's unconditional
    `in_progress -> review -> done`.** P1's `nodes.done` was documented as
    "P0 simplification... criteria evaluation and human review ship P1"
    but P1 didn't actually add real gating either -- every existing test
    (`tests/test_idempotency.py`, `tests/test_rebuild_property.py`) calls
    `done()` without a config and asserts it lands on `done` immediately.
    Changing `done`'s default behavior would have silently broken those.
    Instead `done` grew an optional `config` parameter: omitted, it's
    byte-for-byte the old behavior; supplied (CLI and API always supply
    it, since both already load config on every invocation), it routes
    through `core.risk` and can stop at `review`. This is the "smallest
    correct change" reading of formalizing done -> review for P2 without
    touching an already-tested, already-relied-upon P0/P1 code path.

22. **`review.auto_approved` / `review.awaiting` events are not
    `node.`-prefixed.** `core.rebuild.rebuild_state` folds any event whose
    `type` starts with `"node."` into the replayed `nodes` table by
    reading `payload["id"]` and treating the payload as a full row
    snapshot (see `rebuild.py`'s docstring: "every mutation... appends an
    event whose payload is a full snapshot of the row"). The new P2
    metadata events (`{"tier": ..., "flagged": ...}`) are not full-row
    snapshots, so naming them `node.auto_approved` / `node.awaiting_review`
    would make `rebuild` crash with `KeyError: 'id'` the first time a
    gated `done()` call ran under a property/replay test -- exactly the
    "P1 had to fix this twice" replay pitfall the P2 prompt called out by
    name. Renamed to `review.auto_approved` / `review.awaiting` (a new,
    unclaimed event-type prefix) instead; `tests/test_review.py::test_rebuild_matches_live_through_gated_review_flow`
    covers this.

23. **Human approval of `review -> done` / `review -> in_progress`
    passes the node's own `owner` as the state machine's lease-check
    identity, not the human's identity.** `state_machine.TRANSITIONS`
    marks both edges `requires_owner=True`, meaning "the acting identity
    must equal `nodes.owner`" -- a rule designed for *agent* leases, not
    human approval. `core.gates.approve_spec`/`approve_node` already
    established the pattern of separating `lease_actor` (used only for
    the state-machine check) from `event_actor_role` (what's actually
    recorded in `events.actor`) via `nodes.ready`, where it's moot because
    those edges don't require an owner. `nodes.approve_review` /
    `reject_review` extend the same separation to edges that *do* require
    one: they pass `lease_actor=node["owner"]` (trivially satisfying the
    check, since a human approver isn't the lease holder) while recording
    the real actor (`"human"` by default) in the event. Authorization for
    "is this human allowed to approve" is the session token
    (`core.daemon.verify_session`) checked at the API layer, not the
    node's lease -- these are deliberately different security properties.

24. **Session token lives at `<repo>/.muvue/session`, not
    `~/.muvue/session`.** Plan section 2's file layout lists
    `~/.muvue/session` (home directory, alongside the strict-mode
    airlocks). P0's `repo_init.py`, written before P2 existed, already
    added `.muvue/session` to the *repo's* `.gitignore` -- i.e. the
    established precedent in this codebase is repo-scoped, not
    machine-global. Per "read before writing... match the surrounding
    code's... pattern," `core.daemon` follows P0's existing artifact over
    the plan doc's abstract layout: one token file per repo, which is
    also simpler (no repo-hash keying needed) and matches "one daemon per
    repo" more directly than a single machine-wide file would.

25. **SSE holds one `sqlite3.Connection` open for the life of the
    stream, not a fresh connection per poll.** Verified independently of
    FastAPI/Starlette (a standalone script opening/closing a new
    connection between two `PRAGMA data_version` reads): a **new**
    connection does not reliably observe `data_version` bumps from writes
    committed by other connections, while the **same** connection,
    polled repeatedly, does -- this matches SQLite's actual documented
    semantics for `data_version` ("detect changes... made by another
    database connection" is evaluated relative to a specific connection's
    read state, not the file on disk). The original per-poll-connection
    design would have made the dashboard's live-update mechanism
    silently unreliable. Fixed before it shipped; see
    `tests/test_sse.py` and the CHANGELOG "Fixed" entry. This does not
    reintroduce in-memory daemon state (plan section 1): the held
    connection carries no application state, and losing it on a daemon
    restart costs nothing (a reconnecting dashboard just opens a new SSE
    stream).

26. **`pause`/`resume` are implemented for real in P2 (not left as CLI
    stubs), scoped to what P2 owns.** Plan section 5's "Emergency stop"
    also says `pause` "kills runner processes" -- there is no runner
    yet (P5), so that part is necessarily out of scope. What P2 *can* do
    without the runner is real: `POST /projects/{id}/pause` flips
    `project.phase` to `paused` via the existing `projects.set_phase`,
    and `nodes.start` now refuses while `phase` is `paused` (extending
    its existing `planning`-phase refusal). `resume` reverses it. This
    isn't scope creep -- pause/resume are explicitly named in plan
    section 4's human-verb list and section 8's dashboard views ("pause
    button"), and the mechanism P2 does own (refusing `start`) is a
    faithful, minimal slice of the full "emergency stop" behavior.
