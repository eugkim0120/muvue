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


## P3 decisions

27. **Fixed a pre-existing rebuild bug found while adding P3's new node.\*
    events, not introduced by P3.** `core.rebuild.rebuild_state` assumed
    every `node.*` event's payload *is* the row (`payload["id"]`), but
    P1's `core.gates.edit_criteria` already emitted `node.criteria_edited`
    with a nested `{"node": <row>, "re_approval_required": bool}` payload
    -- `payload["id"]` raised `KeyError` the moment anything ran
    `rebuild.diff_state` after a criteria edit. No P0/P1/P2 test happened
    to combine the two. Fixed by unwrapping a nested `"node"` key when
    present (`tests/test_gates.py::test_rebuild_matches_live_after_
    criteria_edit` pins it) rather than renaming the event (which would
    be a wire-format change with no benefit) -- exactly the "P1/P2 had to
    fix parity bugs" pattern the P3 prompt warned to not skip.

28. **`node.version_bumped`, `commit.linked`, `anchor.hash_requested`,
    `staleness.flagged`, `review.manual_criteria`,
    `review.external_flagged`, `review.auto_check_failed` are new event
    types.** `node.version_bumped` is `node.`-prefixed and carries a full
    row snapshot (like the vast majority of `node.*` events), so it
    replays correctly without special-casing. The rest deliberately are
    *not* `node.`-prefixed (same reasoning as P2's `review.auto_approved`/
    `review.awaiting`, decision #22): their payloads are metadata, not
    row snapshots, and a `node.`-prefixed name would break `rebuild`.

29. **Lease enforcement at `start`-time is an explicit pre-check, not a
    new state-machine edge.** The state machine already refuses a second
    `start` on an `in_progress` node (no `(in_progress, in_progress)`
    edge exists), so acceptance criterion 1 technically already held
    before any P3 change. Added an explicit check anyway, ahead of the
    state-machine call, so the error message says *why* ("leased by X
    until Y") instead of a generic "illegal transition" -- useful for a
    real agent deciding whether to retry, back off, or pick a different
    node, and makes the acceptance criterion's intent ("refused, not
    silently reassigned") legible in the error itself rather than only
    provable by reading `state_machine.TRANSITIONS`.

30. **`nodes.version` bumps on `add_note(actor="human")` and
    `gates.edit_criteria` (any actor), not on every mutation.** The plan
    says "bump on human-visible edits". Read narrowly: an agent's own
    notes/transitions on a node it holds the lease for aren't edits *by
    someone else* (nothing for a version check to protect against); a
    human comment/note or a criteria change landing while the node is
    leased out is exactly the race `expected_version` exists to catch.
    Chose not to bump on every `nodes.*` mutation (e.g. `block`, `fail`)
    since those are already lease-owner-gated by the state machine
    (`NotLeaseOwner`) -- version mismatch is for content drift a human
    caused mid-task, not for lease-ownership races, which already have
    their own, older, more precise error.

31. **`done`/`fail`'s `expected_version` is opt-in (default `None` = no
    check), not mandatory.** Every P0-P2 call site (and most of P3's own
    new tests) never captured a version at `start` time. Making the
    check opt-in preserves every existing test/call site unchanged and
    matches the pattern `request_id` idempotency already established
    (present -> stricter behavior; absent -> old behavior).

32. **Trailer values are read as plain integers (`nodes.id`), not the
    plan's illustrative `"T3.2"`-style label.** No phase before P3 built
    a human-readable task-numbering scheme (that would be a structure-
    layer/naming decision, out of scope); `nodes.id` is the schema's only
    existing node identifier, so it's what a trailer can actually
    resolve against today. A non-numeric trailer token is read as
    unresolvable (silently skipped), not an error -- consistent with
    "trailers are labels, not trusted for binding in light mode": if a
    future phase adds a label scheme, old commits' plain-integer
    trailers keep working and new-format trailers just don't resolve yet
    rather than crashing the hook.

33. **Squashed commits: take every id from every matching trailer line,
    de-duplicated, not just the first or last.** `core.hooks.
    handle_post_commit` only uses this for bookkeeping (`node_commits`
    links + no-op anchor/staleness signals) -- nothing security- or
    approval-relevant reads it. Keeping only one match because a squash
    concatenated several commits' trailers would silently drop real
    links for no safety benefit; taking all of them costs nothing since
    unresolvable/duplicate ids are already handled.

34. **No YAML-aware `.pre-commit-config.yaml` rewriting.** The P3 prompt
    asks to "register with Husky or pre-commit when present" for the
    post-commit hook's *real* behavior. P0 already handles Husky (writes
    into `.husky/post-commit` when `.husky/` exists). Safely rewriting an
    existing `.pre-commit-config.yaml` to add a `repo: local` hook entry
    at the correct list depth needs a real YAML parser -- no such
    dependency exists in this stack (working rule 2: no deps beyond the
    plan's), and hand-editing YAML with string ops risks corrupting a
    user's config for a framework-integration nicety, not a functional
    requirement. `init`/`doctor --repair` still install the shim directly
    into `.git/hooks/post-commit` regardless of whether
    `.pre-commit-config.yaml` exists, so the hook runs either way; it
    just isn't visible inside the pre-commit framework's own hook list.
    Documented here rather than silently skipped; a real implementation
    needs either a PyYAML dependency (a plan-stack decision, not mine to
    make unilaterally) or a hand-rolled YAML-subset writer scoped
    tightly enough to trust.

35. **Core-enforced `HumanOnly`, separate classes in `core.gates` and
    `core.nodes`.** The plan's "never exposed over MCP" was previously
    "enforced by the CLI layer, not here" (P1-era docstrings, literally).
    P3 acceptance #4 requires an adversarial agent script calling a human
    verb *directly against core* to be refused by core. Two identically-
    named `HumanOnly` exceptions (one per module) rather than one shared
    class: `core.nodes` and `core.gates` would otherwise need a new
    cross-import (`nodes` already has no dependency on `gates`; `gates`
    already depends on `nodes`), and a shared exceptions module for two
    call sites is unneeded structure for a single-phase change --
    duplication over the wrong abstraction (working rule/CLAUDE.md: "No
    speculative abstraction").

36. **The MCP server is hand-rolled JSON-RPC over stdio, no MCP SDK
    dependency.** `pyproject.toml` ships no MCP client/server library.
    The P3 prompt explicitly allows adding one "if no MCP SDK dependency
    was pre-approved," but the actual wire protocol MCP's stdio
    transport uses (one JSON-RPC 2.0 message per line, each direction,
    no framing) is simple enough to implement directly and exactly,
    without pulling in a dependency whose exact API surface can't be
    verified against live docs in this environment. `handle_request` is
    pure (request-in/response-out) specifically so its correctness is
    testable without trusting an unverified third-party client library
    to drive it.

37. **Claude Code's hook JSON contract, and Codex/Gemini/Cursor's config
    file formats, are reproduced from memory, not verified.** No network
    access in this environment (see memory: "No network access tools").
    Implemented the most standard/documented-as-remembered shape for
    each, isolated behind `core.claude_hooks`/`core.adapters` so a later
    correction only touches those modules, and flagged explicitly in
    `docs/providers.md` and the P3 handoff report as needing human
    verification before v0.1 ships for real.

38. **Light-mode `auto`-criteria checks are opt-in (`run_checks`
    parameter), not auto-wired into CLI/API.** Plan section 5 says light
    mode's `auto` criteria "run in the checkout" -- taken literally, that
    means actually executing `checks.test`. But wiring that
    unconditionally into every `done` call (CLI/API, which pass `config`
    on every invocation already) would run the project's real test
    command as a side effect of `done`, including against `tests/
    test_api.py`'s fixture repo (a bare tmp dir, not a real pytest
    project, using `criteria_mode="auto"`) -- which would have started
    failing non-deterministically depending on what `pytest -q` does
    when run against an unrelated directory. Chose: build and test the
    real mechanism (`core.review.dispatch`, `core.review.
    default_run_checks`) fully, but leave CLI/API passing no
    `run_checks` (preserving P0-P2's exact behavior: risk tier alone
    decides an auto-criteria node). Wiring a safe, explicit opt-in (e.g.
    a CLI flag, or always-on once the CLI's own working directory is
    guaranteed to be a real checkout rather than a test fixture) is left
    for a follow-up -- flagged in the P3 handoff report.

39. **`project create`/`spec`/`decompose` are thin CLI wrappers, not new
    `core` logic; `decompose` takes `spec_id` (not `project_id`) and
    looks up the project via the spec node.** The dogfood gate run (plan
    section 11) failed at ~62% because the CLI had no verb for project
    creation, Gate 1 spec submission, or Gate 2 decomposition -- only raw
    `core.projects.create_project` / `core.gates.submit_spec` /
    `core.nodes.create_node` calls existed, so a real user of muvue had
    to bypass the CLI to plan a project at all. `decompose` takes the
    approved spec's node id as its positional argument (not the project
    id) because a task's `parent_id` must point at the spec it was
    decomposed from (`core.nodes.create_node(..., parent_id=spec_id)`) --
    the CLI reads `project_id` off the spec node itself
    (`core.nodes.get_node(spec_id)["project_id"]`) rather than asking the
    caller to pass both ids redundantly and risk them disagreeing.
    `--criteria`/`--predicted-touches` are repeatable Typer options
    (`list[str]`) rather than a single comma-separated string (like
    `propose-revision --node-ids`) because criteria text can itself
    contain commas, and repeatable flags are the more natural shape for
    what's usually more than one acceptance criterion per task. Neither
    `spec` nor `decompose` restricts `actor` (both are agent verbs, no
    `HumanOnly` check) -- matching `core.gates.submit_spec`'s and
    `core.nodes.create_node`'s existing signatures, which already default
    `actor="agent"`/`actor="human"` respectively without enforcement; the
    human-only boundary in this codebase is enforced at the *approval*
    verbs (`approve_spec`, `approve_node`, `approve_gate2`), not at
    creation, and that boundary is unchanged by this fix.

    Also added the CLI's missing `approve review:ID` branch
    (`core.nodes.approve_review`) -- the API already dispatched this
    target (`docs/protocol.md`'s Daemon/API section lists it), but the
    CLI's own `approve` target-parsing had no `review` case and no test
    exercised it through the CLI. A few lines, in scope for "make the
    existing CLI surface discoverable," not a new verb.

40. **`.gitignore` marker-block dedup checks exact lines, not
    substrings.** `core.repo_init._update_gitignore` previously appended
    all five `.muvue/*` entries inside its marker block unconditionally,
    even when one was already a plain (unmarked) line in the repo's
    existing `.gitignore` -- a real repo migrating onto muvue commonly
    already ignores `.muvue/muvue.db` or similar by hand. Fixed by
    building `existing_lines` as a `set` of `content.splitlines()` and
    filtering the candidate entries against it before adding the marker
    block. Exact-line matching, not substring matching: a substring check
    (`entry in content`) would have also skipped `.muvue/muvue.db-wal`/
    `-shm` whenever `.muvue/muvue.db` alone was already present, which is
    wrong -- those are three distinct gitignore patterns that happen to
    share a prefix. Chosen as this cycle's dogfood-driven project (see
    the CLI subprocess trail in the P4-readiness report) specifically
    because it was small, self-contained, and required no new `core`
    surface -- a good fit for proving the newly-added `project create` /
    `spec` / `decompose` CLI verbs end to end on a real (if small) piece
    of work.

41. **Airlock path hashes the repo's resolved absolute path, not a git
    remote/init identity.** Plan section 2's file layout table gives the
    format `~/.muvue/airlocks/<repo-hash>.git` but doesn't say what
    `<repo-hash>` is a hash *of*. A repo has no guaranteed remote (muvue
    itself is designed to be "fully removable," plan section 1 principle
    4, and the fixture repos in `tests/fixtures/` have none), so hashing
    `origin`'s URL isn't always available. `core.strict._repo_hash` hashes
    `str(Path(repo_root).resolve())` with sha256, truncated to 16 hex
    characters -- stable across `muvue` invocations (same absolute path
    in, same airlock out) and requires nothing from git itself. Trade-off
    accepted: moving the repo's checkout to a new path orphans its old
    airlock (a fresh one is created transparently at the new path; the
    old bare repo is simply never touched again). No migration tooling
    was written for this -- out of P4 scope, and plan section 12 working
    rule 7 says not to pad phases with unrequested features.

42. **`ensure_airlock` syncs `main` via `git fetch`, not `git push`.**
    The airlock's own `pre-receive` hook rejects any push to
    `refs/heads/main` (that's the enforcement mechanism behind P4
    acceptance criterion 1) -- so if `ensure_airlock` synced by pushing
    from `repo_root` into the airlock, muvue's own housekeeping push would
    reject itself. A fetch (`git --git-dir=<airlock> fetch <repo_root>
    +HEAD:refs/heads/main`) is airlock-initiated, not push-initiated, so
    `pre-receive` never runs against it -- matching the plan's own
    framing that only *agent-initiated* pushes are subject to the
    binding check; muvue keeping its own mirror in sync is not an agent
    push.

43. **Worktree storage path: `~/.muvue/worktrees/<repo-hash>/node-<id>`,
    not inside the repo.** The plan's file-layout table (section 2) only
    specifies the airlock's path, not where per-node worktrees live.
    Putting them under the repo itself (e.g. `<repo>/.muvue/worktrees/`)
    would need `.gitignore`/manifest bookkeeping and risks an agent
    accidentally treating a nested worktree as part of the tracked tree.
    Mirroring the airlock's own `~/.muvue/...` convention keeps every
    strict-mode artifact outside any tracked repo, consistent with "git
    is the only project interface" (plan section 1 principle 4) --
    worktrees are muvue-owned scratch space, not part of what a
    `git status` on the main checkout should ever show.

44. **`protocol_version` is not bumped for P4.** Plan working rule 5:
    "Bump `protocol_version` on any verb change." `core.nodes.start`
    gained two new optional keyword arguments (`config`, `repo_root`) and
    `core.review.dispatch` gained a new optional `diff_files` parameter,
    but every existing call site (CLI, API, MCP, all P0-P3 tests) is
    unaffected: passing nothing for the new arguments reproduces the
    exact P0-P3 behavior (see `tests/test_strict_mode.py::
    test_light_mode_start_never_touches_worktree` and
    `test_strict_dispatch_is_noop_when_no_worktree_bound`). No agent- or
    human-facing verb signature, return shape, or CLI/API surface changed
    from a caller's perspective -- `start`/`done` behave differently
    *internally* by mode, which the plan itself anticipates ("`start`/
    `done` behavior differs by mode internally... the verb contract...
    is likely unchanged", per this phase's own brief). Read the "any verb
    change" in working rule 5 as caller-visible verb contract, not
    internal dispatch, consistent with how P2/P3 already extended
    `nodes.done`'s internals (risk tiers, review dispatch) without a
    protocol bump either.

45. **Driver-unavailable reuses `on_rate_limit`'s `fallback:<agent>`
    config, not a separate `on_unavailable` key.** The plan defines
    `on_rate_limit` (`wait`/`pause`/`fallback:<agent>`) but says nothing
    about a distinct policy for a failed `auth_check` (not logged in, CLI
    missing, etc). "Try a different agent" is the same underlying policy
    either way, and inventing a second, near-identical config key for a
    condition the plan never separately names would be speculative
    abstraction (plan working rule: no unrequested config surface).
    `core.runner._handle_unavailable` reads the *same* agent's
    `on_rate_limit` value; `wait`/`pause` both fall through to a plain
    `block(external)` (there's no meaningful "wait" for a CLI that isn't
    logged in). `docs/protocol.md` documents this explicitly so it isn't
    mistaken for a bug.

46. **The runner pauses globally (schedules nothing new at all) when any
    node in its scope is `awaiting_approval`/`blocked`/`failed`, rather
    than scheduling around them.** P5's acceptance criterion 2 says the
    runner "pauses ... when it hits a node in [that] state" but doesn't
    specify whether that means "skip that one node" or "stop entirely."
    Skipping-and-continuing would need an unstated priority/ordering
    policy (which ready node to try next, whether to keep retrying the
    stuck one) that nothing in plan section 6 defines. The simplest
    reading that stays deterministic and testable: check once per cycle,
    before scheduling, and if anything needs a human, stop -- consistent
    with the plan's framing of these as "inbox" states a human is meant
    to clear (plan section 8 dashboard: "Inbox: questions, blocked,
    review"). See `core/runner.py::_gating_nodes` and
    `tests/test_runner.py::test_run_pauses_without_scheduling_when_a_node_needs_attention`.

47. **`core.runner._ready_nodes` only schedules `task`/`subtask` kind
    nodes, never `spec`.** A `ready` spec node means "Gate 1 approved,
    ready for decomposition" (`core.gates.submit_spec`/`approve_spec`) --
    a different agent workflow (the `spec`/`decompose` CLI verbs, a
    dogfood-gate follow-up) than "spawn a driver to do coding work and
    call `done`." `[routing]` having a `spec` entry doesn't imply the
    unattended runner drives it: nothing in plan section 6 "Runner
    (unattended)" describes automating decomposition, and doing so
    naively (spawning a driver, piping `brief`, calling `done` on
    whatever it prints) would silently mark a spec "done" without ever
    creating the tasks under it -- worse than not automating it at all.
    Scoping `[routing].spec` to a future decomposition-driving feature,
    not this one, is the smallest change that doesn't misbehave; found
    via `tests/test_run_cli.py`'s CLI-level acceptance test, which
    surfaced this by accident (the spec node, also `ready`, was getting
    scheduled ahead of/instead of its own decomposed task).

48. **`core.merge` uses its own `_run_git_as_muvue` wrapper (explicit
    `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/
    `GIT_COMMITTER_EMAIL=muvue@localhost`) for the one git operation that
    creates a commit (`git merge --no-ff`), instead of extending
    `core.strict._run_git`.** `git merge` (unlike every other git command
    `core.strict` already runs -- `worktree add`, `fetch`, `diff`) needs a
    committer identity, and a muvue-initiated merge must not depend on
    the ambient environment having `user.name`/`user.email` configured (a
    daemon process may have none, and did not in this sandbox --
    `_run_git_as_muvue` was added after `attempt_merge` failed with "fatal:
    unable to auto-detect email address" in this environment). Kept
    local to `core/merge.py`, not added as an env override to
    `core.strict._run_git`, since none of that module's own callers ever
    commit -- narrower is the smaller, correct change.

49. **The merge scratch worktree (`_merge_main`) is checked out
    *detached*, not on `refs/heads/main` itself; `attempt_merge` advances
    `refs/heads/main` explicitly with `git update-ref` after a successful
    merge commit.** Discovered via P5's rebuild-first test for the merge
    flow: `core.strict.ensure_airlock`'s own `main`-sync fetch (called by
    every `bind_worktree`, i.e. every node `start`) refuses to fetch into
    a branch that's checked out in another worktree ("refusing to fetch
    into branch 'refs/heads/main' checked out at ..."), which a literal
    `git worktree add <path> main` for the merge scratch worktree would
    trigger the very next time any node starts. A second, independent
    problem the detached design also fixes: `ensure_airlock`'s fetch
    unconditionally resets the airlock's `main` to `repo_root`'s current
    HEAD (correct for "start a new node off real content," P4's use
    case) -- if `attempt_merge` called `ensure_airlock` again on a later
    invocation, it would silently stomp the previous merge commit(s) back
    off `main`. Fixed by (a) never calling `ensure_airlock` from
    `core.merge` (only `strict.airlock_path`, raising `MergeError` if it
    doesn't exist yet -- an airlock must already exist by the time
    anything is `done` and mergeable) and (b) keeping the merge worktree
    permanently detached, advancing `main` by `update-ref` instead of by
    the worktree's own HEAD moving.

50. **`muvue-fake-agent` is a new `pyproject.toml` console-script entry
    point (`src/muvue/fake_agent.py`), not a `muvue fake-agent`
    subcommand.** The P5 prompt left this as an explicit judgment call.
    A subcommand would still be invoked as `muvue fake-agent`, which is
    not how a real vendor CLI (`claude`, `codex`, `gemini`) is shaped --
    it's its own binary on `$PATH`, with its own argv/exit-code/stdout
    contract, no relation to muvue's own CLI. `config.agents.fake.command
    = "muvue-fake-agent"` (the plan's own example config) reads as a
    plain command name, and `core.drivers.invoke_driver` spawns whatever
    `command` says with no special-casing -- a second entry point that
    behaves like an independent process is what makes the driver-
    invocation code path identical for `fake` and for a real vendor CLI,
    which is the point of using `fake` as the stand-in for acceptance
    criterion 1.

51. **`core.nodes.done`'s `review.awaiting` branch also records
    `node.summary_recorded` (full row snapshot).** Found by P5's
    rebuild-first test for the runner's `done` flow (working rule 1:
    write the rebuild test before the mutating code) -- a pre-existing
    P2-era bug, not introduced by P5: setting `nodes.summary` and then
    only recording a `review.awaiting` event (deliberately *not*
    `node.`-prefixed, so `rebuild` doesn't try to treat its
    `{"tier": ..., "flagged": ...}` payload as a row snapshot -- see the
    P2 entry on `review.auto_approved`/`review.awaiting`) meant `rebuild`
    had no event carrying the updated `summary` at all, so replay left it
    `NULL` forever while the live row had it set. Fixed with the
    smallest correct change: one extra `node.summary_recorded` event
    (full row snapshot, so `rebuild` picks it up for free) right after
    the `UPDATE`, before `review.awaiting`. No behavior change from a
    caller's perspective -- same return value, same live-DB state --
    only `rebuild`'s replayed state now matches it.

52. **`close`'s "closeable" gate is "every live (non-soft-deleted)
    `task`/`subtask` node is `done`", excluding `spec` nodes.** The plan
    doesn't say precisely when a project may close; the simplest reading
    consistent with section 9's "wrap up finished work into structure"
    framing is "nothing left undone." Including `spec` nodes in that
    check was tried first and found wrong immediately: a Gate-1-approved
    spec sits at `status='ready'` forever (`core.gates.approve_spec`) --
    it is never itself marked `done`, only the tasks decomposed under it
    are (the same asymmetry `core.runner._ready_nodes` already documents
    at entry #47) -- so requiring it `done` too would make every project
    permanently unclosable. See `core/close.py::_closeable_gate`.

53. **The structure diff is capped at `core.close.MAX_DIFF_ITEMS = 20`
    per category** (decisions / promoted lessons / components), not one
    combined cap. The plan says only "capped per close" with no number or
    shape. Per-category keeps one noisy category (e.g. a project with
    many `predicted_touches` globs) from crowding out the others in a
    single close's PR-diff-sized review, which is the point of the cap
    (section 9: "reviewed ... as the PR diff"). 20 is a round number
    sized for "reviewable in one sitting," not derived from any measured
    review-time budget -- exposed as a module attribute (not a local
    constant) so it's overridable per test/config without a real
    `[close]` config section, which nothing else in the plan asks for
    yet (no speculative config surface, working rule 6).

54. **Promoted lessons (`kind='lesson'`, `pinned=1` notes) are written
    into the `decisions` table, not a separate structure-layer table.**
    The schema (plan section 3, P0) only defines `components`/
    `decisions`/`invariants`/`node_touches` for the structure graph --
    no dedicated "lessons" table, and section 9 lists "promoted lessons"
    as one of three things a structure diff proposes without describing
    a distinct storage shape for them. A lesson's own JSON fields
    (trigger/failure/do_instead/scope, from `core.nodes.fail`) map
    cleanly onto a decision's (title/context/choice): title <- trigger,
    context <- failure, choice <- do_instead, title prefixed `[lesson]`
    so `components.json`/`decisions.json` readers can tell them apart
    from an explicit `kind='decision'` note without a schema change.

55. **Component diff candidates come from `predicted_touches.path_glob`,
    not a real static-analysis/anchor-hashing scan.** Section 9's
    anchor-hash/staleness machinery is explicitly P6+ structure-layer
    work per earlier phases' own notes (see docs/protocol.md's P3
    "husky/pre-commit-framework..." aside), but building a real
    per-language static scanner is squarely P7 `audit` territory (drift
    detection), not P6's "propose a diff to review" scope, and would be
    unrequested scope creep (working rule 7) this early. The only
    structural signal already on hand -- each node's own declared
    `predicted_touches` globs (plan section 3) -- is what `close` uses
    instead: one candidate component per distinct glob a project touched
    that isn't already tracked by name.

56. **History archive events are the same dict shape
    (`{"type", "payload": <json-string>, ...}`) on disk as in the live
    `events` table**, not a pre-parsed/re-shaped export format.
    `core.rebuild.rebuild_state_from_events` was factored out of
    `rebuild_state(conn)` to fold either source through one
    implementation (single write/replay path, plan working rule 3
    extended to replay); keeping the archived shape identical to the
    live row shape (rather than, say, pre-parsing `payload` to a dict
    before writing the `.jsonl.gz` file) is what makes that one function
    usable against both sources without a second code path or an
    extra round-trip.

57. **`import`'s real GitHub fetch is an injectable `fetch_fn(number) ->
    dict` seam, with `data`/`data_path` as the two ways to supply it
    locally today** (no new HTTP-client dependency, plan working rule
    2). The P6 prompt left the exact shape as a judgment call. Three
    options were considered: (a) only a local file (`--data PATH`) --
    simplest CLI story, but gives library/test callers no way to inject
    data without going through the filesystem; (b) only `fetch_fn` --
    clean for tests, but the CLI then has no way to supply data at all
    without a real implementation to inject; (c) both, plus an inline
    `data` dict for API callers who already have the payload in a
    request body. (c) is what's implemented: `POST /import` takes `data`
    directly (no filesystem access from an HTTP body), the CLI takes
    `--data PATH` (a human's likeliest way to hand muvue a mocked/saved
    issue payload), and `fetch_fn` is what a real GitHub-API-backed
    caller plugs in later without changing `import_github_issue`'s
    signature.

58. **`merge --pr`'s body generation is independent of the merge
    outcome** -- it runs (and is tested) even when `attempt_merge`
    returns `"no_worktree"` (light mode). The plan's acceptance
    criterion only asks that the generated body's structure/content be
    correct, not that a real merge occurred first, and gating it on
    `"status": "merged"` would make `--pr` untestable/unusable in light
    mode entirely (the majority of nodes in this codebase's own test
    suite), which is a worse default than describing a node's own
    criteria/decisions/notes regardless of whether a separate,
    strict-mode-only git operation happened to succeed.

59. **`notes.archived_at`, a real `ALTER TABLE` migration step, not just
    a `CREATE TABLE IF NOT EXISTS` column.** P0-P6's `SCHEMA_VERSION`
    bumps only ever added new tables (`CREATE TABLE IF NOT EXISTS` is a
    no-op-safe way to introduce those into a pre-existing DB for free).
    P7 needs a new column on an existing table (`notes`, for lesson-decay
    soft delete). `core.migrate.run_migrate` gained
    `_add_column_if_missing`, an idempotent-by-inspection (`PRAGMA
    table_info`, not catch-the-duplicate-column-error) `ALTER TABLE`
    step, run only when `current < SCHEMA_VERSION`. A dedicated column
    rather than overloading `pinned`/`last_retrieved_at` with a sentinel,
    matching `nodes.deleted_at`'s existing `*_at` soft-delete convention.

60. **Anchor content hashing is `sha256` of `git show <sha>:<path>`'s
    output, not git's own blob object hash (`git hash-object`).** Both
    are valid "content hash" readings of plan section 9's "file path +
    content hash". `git hash-object`'s output is already directly
    queryable via `git ls-tree`/`git rev-parse <sha>:<path>` without a
    `git show` round-trip, which would be the more "native" choice --
    but `core.drift.blob_hash` sticks to plain `sha256(content)` so the
    comparison never depends on git's own object-hashing implementation
    detail (e.g. a future git defaulting to SHA-256 repos instead of
    SHA-1 would silently change every stored anchor hash's meaning if it
    *was* git's object hash; a content hash computed by this module
    stays stable regardless of the repo's own object-hash algorithm).

61. **Reconcile-on-touch (drift loop item 3) matches on
    `predicted_touches` globs against `stale` components' anchor paths,
    not `node_touches`.** The P7 prompt itself names this as the
    documented fallback: `node_touches` (the real per-node
    structure-graph join table) has no populated writer anywhere in the
    codebase as of P7 -- populating it would be its own scope (a
    static-analysis pass mapping a node's actual diff to component ids),
    unrequested by this phase's numbered drift-loop list. `predicted_touches`
    has been populated since P0 and is already what `core.risk` uses for
    every other touch-based signal, so `core.review._stale_touched_component_ids`
    reuses the exact same `risk.touches_globs` overlap check (anchor
    paths as the "touches", the node's globs as the "globs") rather than
    inventing a second overlap primitive.

62. **`audit`'s "oldest-verified" ordering, given `components` has no
    creation/verification timestamp column.** `components.verified_sha`
    is a sha string, not a timestamp -- there's no column to `ORDER BY`
    that means "longest since last verified" directly. `core.drift.run_audit`
    orders by `(verified_sha IS NULL) DESC, id ASC`: never-verified
    components sample first (maximally overdue, no verification signal
    at all), then ascending `id` as a proxy for insertion order among the
    rest (autoincrement ids are monotonic with creation time). Adding a
    real `verified_at`/`created_at` column would be a schema change
    broader than what P7's numbered list asks for; documented here as
    the judgment call instead.

63. **Lesson-decay's "K projects" is counted from `note.retrieved`
    events' distinct `project_id`s, not a dedicated junction table.**
    `core.drift.record_lesson_retrieval` (called from
    `core.queries.brief_node` every time a lesson is surfaced) already
    appends an event with `{note_id, project_id}`; `decay_lessons`
    counts `COUNT(DISTINCT json_extract(payload, '$.project_id'))` over
    those events per note via SQLite's built-in `json1` extension
    (`json_extract`), rather than adding a new `lesson_retrievals(note_id,
    project_id)` table. Consistent with "the events log is the source of
    truth" (plan section 3) and avoids a second write path for what's
    fundamentally an events-log query.

64. **`drift_pct`'s KPI formula is read literally from plan section 9
    ("% components verified within last K commits") even though it reads
    as a *freshness* metric slotted into a field named `drift_pct`.** A
    plausible alternative reading is "the inverse" (fraction *stale*, or
    1 - freshness) so the name and the value's sense agree. The plan
    text gives the formula verbatim, though, and inventing an inverted
    metric under the same name the plan already defined precisely would
    be a bigger judgment call than following the literal text -- kept as
    written, documented here so the naming/sense mismatch is a known,
    deliberate choice rather than a latent bug.

65. **`GET /inbox`'s new drift signals (`"signals"` for unattributed
    commits, `"audit_items"` for `audit`'s drafted diffs) reuse the
    unacked-`events`-row convention, not a new dedicated inbox table.**
    `POST /events/{id}/ack` already exists and already means "remove
    this from whatever inbox-shaped view surfaces it"; `inbox.
    unattributed_commit`/`inbox.audit_drift_signal` are just two more
    event types filtered by `acked_at IS NULL`, the same shape `/inbox`'s
    pre-existing `questions`/`review`/`blocked` slots already follow
    (`questions` filters `status = 'open'` instead, but the "still
    outstanding" framing is the same). A new table would duplicate what
    `events` (append-only, already replayable, already ack-able) already
    provides.

66. **The P8 VS Code extension assumes `muvue serve` is already running
    at a configurable URL (`muvue.daemonUrl`, default
    `http://127.0.0.1:8765`) and never spawns or manages that process
    itself.** Plan section 11's P8 row scopes the deliverable as "webview
    + command bridge," and section 8 frames the extension purely in
    terms of the dashboard "load[ing] unchanged inside a VS Code
    webview" -- neither mentions process lifecycle, and P2 already gave
    `serve` its own CLI entry point and restart-safety story (reconcile-
    on-start, no in-memory daemon state) that a second, extension-owned
    spawn path would either duplicate or race against. Simplest reading:
    the extension is a thin client of an independently-run daemon, same
    as a browser tab would be.

67. **The extension's webview renders the dashboard via an `<iframe
    src="<daemon-url>">`, not a fetch-and-inject of the HTML.** Fetching
    `index.html`'s markup and re-injecting it into the webview's own DOM
    would strip it of its own origin (the fetched JS's relative `fetch()`
    and `EventSource("/events/stream")` calls, see `index.html`, need to
    resolve against the daemon's origin, not `vscode-webview://...`) --
    an iframe pointed straight at the daemon's URL keeps the page
    same-origin with itself and requires no changes to `index.html` or
    the daemon (P8 acceptance #1: "same `index.html` renders unchanged").
    The webview's own wrapper HTML (`buildWebviewHtml` in
    `vscode-extension/src/lib.ts`) sets `frame-src` to just that one
    origin and `default-src 'none'` otherwise -- it runs no script of its
    own, since all dashboard behaviour, including the SSE connection
    (P8 acceptance #2), lives inside the iframe unchanged.

68. **No CORS header was added to `src/muvue/api/app.py` for P8.** The
    only cross-origin surface an extension could plausibly need CORS for
    is a webview-script `fetch()` to the daemon from the webview's own
    `vscode-webview://` origin; this extension does not do that -- the
    iframe navigates directly to the daemon's URL (decision #67, so its
    `fetch`/`EventSource` calls are same-origin with the daemon, not
    cross-origin), and the three commands' own daemon calls run in the
    extension host's Node process (`extension.ts`'s `fetch`), which is
    not subject to browser CORS at all. Plan working rule 3 says to fix a
    genuine CORS gap minimally if one is found; none was found, so
    `app.py` is unchanged in this phase.

69. **Real GitHub API wiring for `import`/`merge --pr --create` (post-P8,
    remediation pass).** P6/P8 built `import`'s `fetch_fn` seam and
    `merge --pr`'s body generation as network-free stubs because those
    build sessions had no network access. This environment's session
    does have an already-authenticated `gh` CLI, so the seam is filled
    for real: `core/github.py` shells out to `gh issue view`/`gh pr
    view`/`gh pr create` (never touching GitHub credentials directly --
    plan section 1 principle 7, same posture as the vendor-agent
    drivers in `core.drivers`). `muvue import --from github#N` now
    fetches live by default (`--data PATH` still available to bypass
    `gh` entirely); `muvue merge --pr --create` opens a real PR
    (`--pr` alone still only returns body text, no write). Verified
    live end-to-end against a real throwaway GitHub issue (created and
    closed during verification, not left open) and a real CLI-driven
    `project create -> spec -> approve -> decompose -> import` loop;
    `gh pr create` itself was left to its existing mocked-subprocess
    unit tests rather than opening a real throwaway PR, since a PR is a
    more externally-visible artifact than an issue and the two `gh`
    call shapes (`issue view`/`pr view` vs `pr create`) are structurally
    identical subprocess wrappers, so the live issue-fetch verification
    already covers the mechanism.

70. **`read_txn` is a real (deferred) transaction, not a no-op, but it
    is never the thing a P0 acceptance criterion depends on.** v4
    section 1.2 requires exactly two context managers, `read_txn`/
    `write_txn`, and calls only `write_txn` load-bearing. The simplest
    reading consistent with "exposes exactly two" (not "one, plus a
    documented no-op") is that `read_txn` should actually do something:
    it opens `BEGIN DEFERRED` so a caller running several `SELECT`s
    gets one consistent snapshot instead of each statement picking up
    whatever the latest committed state happens to be mid-read. It is
    nesting-safe the same way `write_txn` is (checks `conn.in_transaction`
    first). No existing call site in this codebase needed converting to
    use it this session -- multi-statement reads that already existed
    (e.g. `core.queries.brief_node`) don't currently require snapshot
    isolation across their several `SELECT`s, so `read_txn` ships as
    infrastructure for future callers, not retrofitted everywhere reads
    happen. See `core/db.py`.

71. **`write_txn` nests by checking `conn.in_transaction`, not via an
    explicit depth counter or `SAVEPOINT`.** `muvue.core` functions
    routinely call other `muvue.core` functions that are themselves
    wrapped in `write_txn` (`core.nodes.fail` calls `core.nodes.
    add_note`, `core.gates.approve_gate2` calls `core.gates.approve_node`
    in a loop, `core.close.close_project` calls `core.projects.
    set_phase`, etc.). Requiring every one of those ~50 mutating
    functions to only ever be called at the "top" of a transaction would
    have meant either duplicating every helper's logic inline at each
    call site (bad) or building a `SAVEPOINT`-based nested-transaction
    scheme (over-engineered for what's actually needed here: the plan
    only asks for one write lock acquired up front, not partial
    rollback of an inner helper while keeping the outer transaction
    alive). `conn.in_transaction` is sqlite3's own already-correct
    answer to "is a transaction currently open on this connection" --
    the outermost `write_txn` call opens `BEGIN IMMEDIATE` and owns
    commit/rollback; every nested call just becomes a no-op passthrough
    that shares the same transaction. This is why "smallest correct
    change" wins here: no new abstraction, just the right check.

72. **`agent_spend`'s unit is decided by `[agents.<x>.cost_model]`, via
    a small `_COST_MODEL_TO_SPEND` lookup in `core/runner.py`, not by a
    new `[agents.<x>.budget]` config section.** v4 section 2 introduces
    `[agents.<x>.budget]` (`unit`/`limit`) as the thing a *future*
    budget-enforcement phase validates and checks against -- this
    session's scope is explicitly narrower: "just add the table, a
    minimal core.spend ... helper ... with a test," not the config
    section or the enforcement logic. Rather than adding an unused
    config field now (speculative, since nothing reads it yet), spend
    accrual derives its unit directly from the one config value that
    already exists and is already meaningful (`cost_model`):
    `usd -> cost`, `tokens -> in_tokens + out_tokens`, `quota ->
    requests`. When the budget-enforcement phase lands, it can add
    `[agents.<x>.budget]` and validate `budget.unit` against this same
    mapping (`doctor` erroring if they don't line up, per v4 section 2's
    own "`doctor` errors if `budget.unit` is not producible by that
    driver's `cost_model`" rule) without this table or its accrual path
    changing at all.

73. **`actor_evidence` defaults are assigned per calling *layer*, not
    threaded as a live TTY/parent-process detection at each call site.**
    v4 section 3/4 explicitly defers real detection ("walk the parent
    process chain for a known agent CLI") to future dashboard-surfaced
    work and asks only that this session "thread a correctly-defaulted
    `actor_evidence` string through every record_event call site."
    Simplest reading: every `muvue.core` function that can mutate state
    and is reachable from more than one layer takes an `actor_evidence`
    parameter defaulting to `"tty"` (CLI is the dominant/original
    caller for all of them, and CLI invocation is, almost by
    definition, "some real or simulated terminal" -- full TTY detection
    is exactly the deferred work), and the three other entry-point
    layers override it explicitly at their own call sites:
    `mcp_server.py` passes `"mcp"` for every one of its 7 tool handlers,
    `api/app.py` passes `"dashboard_token"` for every one of its ~14
    mutating endpoints (the daemon's session-token auth is the actual
    evidence for *every* API-originated write, human-verb or
    agent-verb alike -- see decision below), `core/runner.py` passes
    `"subprocess"` at each of its ~9 call sites into `core.nodes`/
    `core.spend`, and git-hook-driven paths (`core/hooks.py`,
    `core.drift`'s hook-invoked functions) hardcode `"hook"` inline
    (never parameterized -- they are *only* ever reached from a hook
    shim, so there is nothing to override). `core.daemon.
    reconcile_leases`/`process_queue` (the daemon's own background
    loop, not triggered by any of the four request-shaped layers) also
    use `"subprocess"` -- the closest of the five named values to "a
    non-interactive background process," logged here since the plan's
    own four examples (tty/dashboard_token/mcp/hook) don't cover a
    daemon's unprompted background action explicitly.

74. **Every API-layer mutation gets `actor_evidence="dashboard_token"`,
    including the API's own agent-verb endpoints (`start`/`done`/`fail`/
    `ask`/etc.), not just its human-verb ones.** `actor_evidence`
    describes *how the actor was determined* -- i.e., what process
    talked to `muvue.core` and on what authority -- not the verb's
    protocol-level role (agent vs. human). Every mutating endpoint in
    `api/app.py` is reached the same way: an HTTP request to the daemon,
    which (per v4 section 8a, a separate follow-up session's scope, but
    already true of the pre-existing session-token mechanism this
    session didn't touch) is gated by the same token regardless of
    which verb it calls. So the evidence is uniform across the whole
    file: "a request that reached the daemon," i.e. `dashboard_token`,
    is the honest answer for all of it -- distinguishing "agent-verb
    API call" from "human-verb API call" evidence-wise would invent a
    finer-grained taxonomy the plan's five named values don't have.

75. **`core.events.ack_event` added; `api/app.py`'s `POST
    /events/{id}/ack` now calls it instead of issuing `UPDATE events SET
    acked_at = ...` inline.** Found during this session's mutating-SQL
    audit (working rule 3: "If you find yourself writing SQL elsewhere,
    stop"): this one pre-existing endpoint (shipped in P2, before this
    rule existed in v3) wrote directly against `events` from
    `api/app.py`, bypassing `muvue.core` entirely -- the one gap the
    codebase-wide `write_txn` sweep this session did surfaced. Fixed in
    place as part of "go slowly and completely, don't leave any
    mutating path on the old pattern," since the whole point of this
    session is exactly this invariant.

76. **`docs/threat-model.md` ships as a placeholder stub this session,
    not a real threat model.** v4 working rule 6 lists it as a newly
    required file, but the actual content (daemon attack surface,
    binding/origin/CSRF/token analysis) is squarely v4 section 8a's
    scope, explicitly a separate follow-up session per this session's
    own task boundary ("do NOT touch ... daemon security"). Writing a
    real threat model now would mean either doing 8a's analysis early
    (out of scope, and premature -- 8a's own session should own it) or
    writing something that reads like a threat model but isn't one
    (worse than an honest placeholder). The stub says exactly this and
    points at section 8a.

77. **`rebuild.diff_state`'s replayable-projection filtering applies to
    the *live* snapshot too, not only the replayed one.** `core.rebuild.
    live_state` still snapshots every column of `nodes`/`notes` (full
    row shape, used elsewhere e.g. by `core.history`'s replay target);
    `_diff_tables` is what narrows the comparison to the replayable
    subset (v4 section 3), so it must project *both* `live_row` and
    `replayed_row` down to the same `cols` before comparing -- projecting
    only one side would make every comparison spuriously mismatch (a
    live row has more keys than a replayable-only projection) or
    spuriously match (comparing a full dict against a partial one is
    never equal, which would make the "genuine bug" test fail for the
    wrong reason). Fixed as part of the section 3 redefinition, verified
    by both new tests in `tests/test_rebuild_property.py`.

78. **`agent_spend`'s replay coverage is out of scope this session.** v4
    section 3 lists "spend" among the replayable projection's
    categories, but wiring `agent_spend` into `core.rebuild`'s
    diff/equality check (a new `_REPLAYABLE_AGENT_SPEND_COLUMNS` entry,
    `rebuild_state_from_events` folding `spend.recorded` events, etc.)
    is real, non-trivial work this session's explicit scope boundary
    (section 2: "just add the table ... with a test" -- not the
    budget-check logic, not general rebuild wiring) doesn't ask for.
    `agent_spend` rows are written through `write_txn` and logged as
    `spend.recorded` events (so a *future* rebuild-integration phase has
    the event stream it needs already in place), but `rebuild.diff_state`
    does not yet compare them. Flagged prominently for whichever
    follow-up session extends replay coverage to `agent_spend`.

79. **`PYTHONPATH=<dir>` prefix added to every hook shim command,
    discovered empirically, not anticipated from the plan text alone.**
    v4 §4a says `-S` (skip `site` init) is "part of the latency win";
    testing the actual installed shim (not just an in-`src/`-directory
    subprocess invocation) showed `-S` also breaks `import muvue._hook`
    outright, because `site` is what normally puts an installed
    `muvue` on `sys.path` (this dev venv's editable install; a real
    `uvx muvue init` install would rely on it identically for a normal
    site-packages install). Fix: `core.repo_init.
    hook_fast_path_command` computes `muvue.__file__`'s grandparent
    directory once (at shim-write time) and prefixes the shim command
    with `PYTHONPATH=<dir>` -- PYTHONPATH is applied by the interpreter
    before `site` would run, so it survives `-S` untouched, and this
    still measures well within the p95/p99 budget (see
    `tests/test_hook_latency_benchmark.py`). Single source of truth
    shared by the git-hook shim writer (`core/repo_init.py`) and the
    Claude Code adapter config writer (`core/adapters.py`) so they
    can't drift apart.

80. **`SessionStart`'s synchronous `additionalContext` brief injection
    and `Stop`'s synchronous "block on unlogged in_progress work" check
    are dropped, not preserved via some exemption.** v4 §4a's own text
    is unambiguous: "Default action is append one JSON line to
    `.muvue/queue.jsonl` and exit" for every event except `PreToolUse`,
    with no carve-out for other events that happen to also do something
    useful synchronously. Read literally (working rule 7's "simplest
    reading" for an ambiguous item, though this one isn't very
    ambiguous): both `core.claude_hooks.session_start` and `.stop` were
    already read-only advisory checks with no DB *write* to defer in
    the first place, so nothing about them was silently orphaned mid-
    write; what's lost is purely a synchronous check that must now wait
    for a drain to become visible (as a `hook.<event>` audit event),
    which is too late to have blocked the turn it was about. This is a
    real, deliberate reduction in enforcement strength for those two
    checks in exchange for the fast path's latency guarantee. Flagged
    for whichever follow-up session (daemon security, §8a, or a future
    enforcement-relocation phase) wants to restore synchronous
    enforcement for `Stop` specifically -- e.g. by having the daemon's
    continuous drain surface a *blocking* notification back to the
    client out-of-band, which is out of this phase's scope to design.

81. **`post-commit`'s spooled queue line carries only `{"event",
    "ts", "sha"}` -- no `message`/`files`.** v4 §4a: "everything else
    about that commit's diff can be re-derived from git when the queue
    is drained." `handle_post_commit_from_git_sha` (a new sha-
    parameterized sibling of the pre-existing `handle_post_commit_from_
    git`, sharing all its logic) re-reads the commit's message and
    touched files from git at drain time, using the spooled sha rather
    than always assuming HEAD -- drain may run after several commits
    have queued back to back, by which point HEAD has moved past the
    first one. `muvue._hook` itself resolves the sha by reading
    `.git/HEAD` and its ref chain directly (with a `packed-refs`
    fallback) rather than shelling out to `git rev-parse HEAD`:
    `subprocess` is not on this module's stdlib allow-list (`sys`,
    `os`, `json`, `time`, `sqlite3` lazily), and spawning a `git`
    process on every commit would eat into the latency budget the fast
    path exists to protect.

82. **Non-`post-commit` spooled events (`session-start`, `pre-compact`,
    `stop`, `pre-push`, `hook_timeout`) are recorded at drain time as a
    generic `hook.<event>` audit event via `events.record_event`, not
    given individual per-type drain handlers.** There is no existing
    full handler for these to "reuse, don't reimplement" the way
    `post-commit` has one (`core.claude_hooks.session_start`/
    `pre_compact`/`stop` are read-only decision functions, not
    mutations -- see decision #80): writing one now would be inventing
    processing logic the plan doesn't ask for (working rule 8: "no
    unrequested features"). A single generic audit-trail record
    satisfies v4 principle 10 ("detection everywhere else") -- a
    `hook_timeout`, for instance, stays visible in the events log even
    though nothing currently blocks on it -- without guessing at
    future per-type behavior. `node_id` is validated against the live
    `nodes` table before use (dropped to `NULL` if it no longer
    resolves) since the queue file is untrusted input a hand-edit or a
    stale `.muvue/current_node` could corrupt.

83. **The daemon's "continuous drain loop" (v4 §4a) rides the existing
    SSE `/events/stream` generator in `api/app.py`, not a new
    standalone loop.** No dedicated daemon process/background-task
    loop exists yet -- that's P2a scope (§11: "Daemon core: ... queue
    processing, process management, security controls of §8a"),
    explicitly a separate follow-up session this phase must not touch.
    The SSE generator is the only continuous, restart-safe loop the
    daemon runs today, so the bounded drain (`core.hooks.drain_queue`)
    rides along as another periodic task inside it per iteration
    (~every 100ms), guarded by its own try/except so a drain failure
    never breaks the SSE stream. Uses a *second* SQLite connection
    distinct from the one polling `PRAGMA data_version`: a write
    committed by a connection is not reliably visible in that same
    connection's own subsequent `data_version` read (the same
    single-long-lived-connection caveat already documented for the
    `data_version` poll itself applies symmetrically to a writer on
    that connection). Whichever P2a session builds the real daemon
    process/task structure will likely want to move this drain call
    into that structure instead; flagged here so that session knows
    exactly where today's version lives and why it needs its own
    connection.

84. **Session-token gating (v4 §8a) was extended to every mutating
    endpoint, including the agent verbs that P2/P2b deliberately left
    unauthenticated.** The original P2 design gated only what it called
    "human verbs" (`approve`/`reject`/`ack`/`merge`/`close`/`pause`/
    `resume`/`handoff`/`import`), on the documented theory that agent
    verbs are "CLI-equivalent" and this is a single local daemon with
    no multi-machine sync. v4 §8a's own framing makes that theory
    untenable for one specific endpoint: `POST /nodes/{id}/start?
    agent=X` launches an arbitrary configured agent CLI subprocess, and
    the plan text names it explicitly as "a remote-code-execution
    surface reachable from any web page the user has open" -- it does
    not carve out an exception for the fact that `start` is nominally
    an "agent verb". Leaving `start` (and, by the same logic, `done`/
    `fail`/`ask`/`wait`/`replan`/`comment`/`propose-revision`, which
    all mutate state a same-origin attacker has no business mutating)
    unauthenticated while gating `pause` would have been an arbitrary,
    indefensible line. Simplest reading consistent with the plan's own
    stated threat (working rule 7): gate everything that mutates.
    `GET` endpoints remain unauthenticated -- v4 §8a's own acceptance
    text ("Daemon security tests pass... each must 403 before any side
    effect") is scoped to mutation, and the daemon is still a
    single-user, no-multi-machine-sync tool where read access to your
    own project's state was never the threat model.

85. **Idle timeout (control 6) is read as "since the last request", not
    "since token issuance"**, per the plan text's own wording: "idle
    sessions expire after 8h" -- "idle" has one natural reading, and
    the plan's working rule 12 explicitly asked for this call to be
    made and documented rather than defaulting to whichever is easier
    to implement. `SessionManager.verify_and_touch` therefore resets
    `last_activity` to "now" on every *successful* verification, so a
    session used at least once every 8h never expires for the
    lifetime of the `serve` process; one left untouched for 8h+ does.
    An absolute (issuance-based) expiry was considered and rejected: it
    would force a human actively working through a long review session
    to re-exchange the fragment mid-task for no security benefit over
    the idle model, since the token still rotates completely on every
    `serve` restart (control 6's other half) regardless of which idle
    policy is chosen.

86. **The session token's cookie value equals the token itself, not a
    separately-derived session identifier.** v4 §8a control 5's text
    describes minting one 256-bit token and exchanging it for a cookie,
    without specifying whether the cookie carries that same value or a
    second, server-generated session id mapped to it. Simplest reading
    (working rule 7): reuse the one value. A second indirection would
    only matter if the token and the cookie needed independent
    lifecycles (e.g. multiple concurrent cookie-holding sessions off
    one token) -- v4 explicitly does *not* want that ("one human
    session is meaningful per repo at a time", plan §2's session-token
    framing, unchanged from v3 on this point) -- so a second layer of
    indirection would be unrequested complexity (working rule 8). Both
    the `Authorization: Bearer <token>` path (non-browser clients: CLI,
    VS Code extension, curl, `doctor`'s probes) and the cookie path
    (the dashboard, after exchange) therefore authenticate against the
    exact same `SessionManager.verify_and_touch` check.

    A related, purely mechanical decision landed in the same commit:
    `create_app`'s daemon-security `Host`/`Origin` middleware needs to
    know the daemon's own bound port to validate against, but FastAPI's
    `TestClient` defaults to `Host: testserver` with no real port at
    all. Rather than special-case tests, `create_app(..., port=None)`
    (the default) validates only the loopback *hostname* portion of
    `Host`/`Origin` and skips exact-port matching; a real `muvue serve`
    process always passes its actual `port`, so the exact-port check is
    live in production and is covered against a real daemon in
    tests/test_daemon_security.py. Existing `TestClient`-based tests
    were updated to use `base_url="http://127.0.0.1"` (matching the
    hostname check) rather than the httpx default `http://testserver`.

87. **`doctor`'s live security probes (control 7) spin up a throwaway
    daemon against an isolated scratch repo, never `repo_root` itself,
    when nothing is already listening -- and this runs by default,
    not opt-in.** The plan text explicitly leaves the "skip vs. spin up
    a throwaway daemon" choice to be made and documented (working rule
    12/§8a control 7: "decide sensibly... and document your choice as
    a decision"). A skip-only `doctor` would silently stop verifying
    this phase's own controls on precisely the machine state that's
    most common right after `muvue init` -- no daemon running yet --
    which defeats the point of "fails loudly" in the plan text.
    Spinning the throwaway daemon up against `repo_root` itself was
    tried first and rejected after it broke existing queue-drain tests
    (tests/test_cli_drain_callback.py,
    tests/test_doctor_queue_depth.py): `muvue serve` runs
    `reconcile_on_start` (lease reconcile + queue drain) at startup by
    design (plan §8), which is exactly correct for a *real* daemon but
    is an unacceptable side effect for a read-only diagnostic command
    to have on the repo it's diagnosing. The throwaway daemon now
    serves a fresh `init_repo`'d `tempfile.TemporaryDirectory()`
    instead, fully decoupled from `repo_root`'s actual DB/queue state;
    controls 2/3/4 don't depend on which repo the probed daemon happens
    to be serving, only on its HTTP-layer behavior. Existing
    queue-drain-focused tests were updated to pass
    `skip_security_probes=True`/`--skip-security-probes` to stay
    decoupled from this orthogonal new behavior; a dedicated
    tests/test_doctor_security_probes.py covers control 7 itself,
    including the "no side effect on the real repo" property and the
    already-running-daemon branch (probed directly, no throwaway spun
    up). `doctor`'s CLI also gained `--daemon-port` so a non-default
    `serve --port` can be probed correctly.

    A second, unrelated bug surfaced while building the throwaway-
    daemon integration tests for this phase: `cli.main.serve` printed
    its "listening on" readiness line *before* calling
    `uvicorn.run(host=, port=)`, which only actually binds and starts
    accepting connections some time after that call is made --
    `tests/test_serve_integration.py`'s pre-existing pattern of reading
    that stdout line as a readiness signal was therefore racy (it
    happened not to be hit before this phase's tests, which spawn many
    daemons back-to-back). Fixed by binding a real listening socket
    (`socket.bind()` + `socket.listen()`) *before* printing the line,
    then handing its file descriptor to `uvicorn.run(fd=...)` --
    "readiness line printed" and "socket accepts connections" are now
    the same moment for every caller of `muvue serve`, not just this
    phase's own tests.

88. **The granularity-lint hard block (v4 §5, changelog item 9) reads
    "no `auto` criterion" the same way #12 already resolved it for the
    warning it upgrades**: at the node-mode granularity, `criteria_mode
    != "auto"`, not a per-individual-criterion-string check. The schema
    still stores one `criteria_mode` enum per node (a list of plain
    strings in `criteria_json`, no per-string mode), so a per-criterion
    reading was never available to begin with -- #12's reasoning applies
    unchanged, just at a different (now hard-block, not warn) severity
    for medium/high tier. `core.gates.approve_node` checks this using
    the node's already-current `risk_tier` (freshly computed on initial
    Gate 2 freeze; whatever `core.gates.edit_criteria` forced it to on a
    re-approval) rather than a value recomputed inline, so the block
    applies identically on both the initial-freeze and re-approval
    paths -- a criteria edit that keeps a node non-`auto` and forces it
    to `high` can't reopen the loophole by being waved through on
    re-approval. Low tier is left exactly as `lint_task` already warns
    it (v4's own wording names only "medium- or high-tier").

89. **`touches_outside_predicted` is scored at `core.nodes.done`'s tier
    recompute, never at Gate 2 approval.** v4 §5 lists it as one of
    several risk-tier inputs without saying at which call site to
    compare it; the plain reading is the only one that's even
    computable: `actual_touches` (real commits) doesn't exist yet at
    Gate 2 approval time, before any work has started, so there is
    nothing yet to compare `predicted_touches` against. `core.risk.
    touches_outside_predicted(conn, node_id)` is a pure query (any
    `actual_touches` row whose `path` matches none of the node's
    `predicted_touches` globs); `core.risk.compute_tier`'s new
    `touches_outside_predicted` keyword only ever raises the touch-count
    tier to at least `medium` via the existing `max_tier` never-
    downgrade helper, consistent with v4 §13's own framing
    ("`predicted_touches` is a heuristic... not a safety guarantee" --
    scored, not hard-blocked, matching working rule 8's "no unrequested
    features": the plan never asks for a hard block here, only P2b's
    acceptance line "touch outside `predicted_touches` raises the
    tier").

90. **Branch coherence (v4 §5, changelog item 12) reuses `core.nodes.
    start`'s existing `config`/`repo_root` optional keyword arguments**
    (added for P4's strict-mode worktree binding, decision #44) rather
    than adding new ones, and is checked *before* `core.strict.
    bind_worktree` runs so a strict-mode refusal never leaves an orphan
    worktree on disk. Per decision #44's own precedent, this is read as
    an internal behavior extension of an existing verb, not a
    caller-visible contract change -- `protocol_version` is **not**
    bumped for this session's three deltas, same reasoning as #44:
    every existing call site (light mode, no divergence, or no
    `repo_root` passed at all) reproduces its exact prior behavior.

    `core.nodes.start`'s API (`POST /nodes/{id}/start`) and MCP (`start`
    tool) entry points previously called `core.nodes.start` with neither
    `config` nor `repo_root` at all -- meaning strict-mode worktree
    binding, not just this phase's branch check, was silently never
    exercised through those two paths. Wiring both through (API: already
    had `config`/`repo_root` in `create_app`'s closure; MCP: `_call_tool`
    now special-cases `"start"` to pass `repo_root`, the only verb whose
    handler needs it) was necessary to make "every `start`" (v4 §5's own
    wording) actually true, not just the CLI's. Fixing the *strict-mode*
    gap this incidentally exposes is in scope only as far as making the
    branch check reachable everywhere `start` is callable -- no other
    strict-mode behavior was added or changed at the API/MCP layer this
    session.

    `projects.branch` (SCHEMA_VERSION 4 -> 5, `core.migrate` `ALTER
    TABLE`s it into existing databases) is `NULL` whenever
    `create_project` isn't given a `repo_root` -- every pre-v4 call site
    and most unit tests -- which is read as "no baseline recorded", not
    an error; the coherence check is then a no-op rather than treating a
    missing baseline as a divergence. `core.rebuild`'s project-row
    projection (`_PROJECT_COLUMNS`) gained `branch`: it's set once, at
    creation, and carried unchanged in the `project.created` event
    payload, so it's fully within the replayable projection (v4 §3) with
    no new exclusion needed.

91. **Per-driver budget is scoped to the driver across every project,
    not per (project, driver).** `agent_spend` is keyed `(project_id,
    agent, unit)` (decision #72), but `[agents.<x>.budget]` lives in
    `config.toml`, which is repo-wide, not per-project -- there is no
    config surface for "this project's budget for this driver" distinct
    from any other project's. `core.runner.driver_budget_state` sums
    `agent_spend.spent` across every project for a given `(agent, unit)`
    pair before comparing to the configured limit. The alternative (only
    counting the current `run()` call's own `project_id`, when given)
    would let a driver silently reset its spend history every time a
    caller scopes `run` to a different project, which contradicts "each
    driver carries its own budget" (v4 §2) reading as one number *for
    that driver*, not one number per project the driver happens to touch.

92. **`select_batch`'s new `exhausted_agents` parameter is optional and
    self-computing, not a required argument.** Existing tests (and any
    other direct caller) invoke `select_batch(conn, ready_rows, config,
    parallel=..., agent_override=...)` without it; adding a required
    parameter would have broken every one of those call sites for no
    behavioral gain, since the value is always deterministically
    derivable from `conn`/`config` (`core.runner._exhausted_agents`).
    `core.runner.run` still passes it explicitly (computed once per
    cycle) purely to avoid re-querying `agent_spend` a second time
    inside `select_batch` on the same cycle -- an optimization, not a
    behavior difference.

93. **A driver-budget-exhausted stop is not a distinct `paused` reason.**
    v4 §6's own wording ("don't build a separate 'no path left'
    detection, let it fall out of 'there's nothing else routable'") is
    read literally: when every remaining ready node routes to an
    exhausted driver, `select_batch` returns an empty batch and `run`'s
    existing `if not batch: break` (unchanged) exits with `paused =
    None` -- the same code path as "nothing is `ready`" or "everything
    already got scheduled this session." The caller can still tell a
    driver was the reason by reading the returned `"budget"` dict for
    `exhausted: true` entries; no new top-level reason string was added.

94. **`max_wall_clock_minutes` / `max_nodes_per_run` are checked at the
    very top of every cycle, before the gating check and before any
    driver-budget check.** The plan doesn't order these relative to each
    other; unit-free stop conditions were read as the outermost,
    cheapest, most "the operator said stop no matter what" check --
    checking gating or per-driver budget first would mean a run sitting
    on a gating-blocked node for a long time could silently blow through
    `max_wall_clock_minutes` before ever reporting it, which defeats the
    point of a wall-clock ceiling.

95. **`run()` gained a `now_fn` parameter (default `core.runner._now`),
    threaded through `run_node`/`_apply_rate_limit`/
    `_apply_on_rate_limit`/`_handle_unavailable`, rather than a second,
    separately-injectable clock for rate-limit timing alone.** One clock
    seam matches the existing codebase convention (`core.asks.wait`'s
    `now` parameter, `core.hooks._drain_clock`) and lets a single test
    fixture drive both `max_wall_clock_minutes` and
    `max_wait_minutes`/`wait_started_at` timing deterministically without
    two independent fakes to keep in sync.

96. **`wait_started_at` "episode continuity" is determined by scanning
    for any event on the node, after the latest `runner.rate_limited`
    event, whose type is not `node.ready`/`node.start`.** A `wait`
    episode spans reconcile-unblock -> re-`start` -> re-block cycles,
    each of which only ever emits `node.ready` (from
    `reconcile_rate_limits`/`_apply_on_rate_limit`'s ready-then-retry)
    and `node.start` (from `run_node`'s own `nodes.start` call) in
    between two `runner.rate_limited` events, as long as the node keeps
    getting rate-limited. Anything else appearing between them (a real
    `done`, a different block reason, a handoff) means the node did
    something other than sit in the same stalled `wait` loop, so the
    next `runner.rate_limited` event starts a fresh episode rather than
    inheriting a stale `wait_started_at`. This is a event-log scan, not a
    new schema column, matching how `reconcile_rate_limits` already
    reads `retry_at` back out of the latest `runner.rate_limited` event's
    payload rather than adding a dedicated column for it.

97. **`runner.rate_limit_wait_exhausted` (the "notification fires" for
    `max_wait_minutes` timeout escalation) is a `write_txn`-recorded
    event, not a call into `config.notify.url`.** The P5 prompt names
    this as the documented fallback ("if no real notification-sending
    exists yet (it may have been dashboard-only), a
    `runner.rate_limit_wait_exhausted` event recorded via `write_txn` is
    the minimum acceptable 'notification fires' implementation").
    Checked: no code path in this repo actually performs an HTTP call to
    `config.notify.url` anywhere yet (grep for `notify.url` /
    `requests`/`urllib` outside `core.doctor`'s probe helper turns up
    nothing) -- P2's dashboard/inbox work surfaces inbox events in the
    UI, it does not send them anywhere. Wiring a real webhook call is out
    of scope for this session (not one of the four requested deltas) and
    is left for whichever future session actually builds
    `config.notify.url` delivery.

98. **`AgentConfig.on_rate_limit_timeout` explicitly forbids `"wait"`,
    validated at config-load time.** v4 §6 doesn't spell this out in so
    many words, but its own reasoning for adding `max_wait_minutes` in
    the first place -- "an unbounded `wait` ... is a silent stall" -- 
    applies identically to a *timeout* action of `"wait"`: it would mean
    "wait past the wait timeout by waiting some more," which is not a
    timeout at all. Rejected at the same `field_validator` layer as
    `on_rate_limit`'s existing `wait`/`pause`/`fallback:<agent>` check,
    with the same error-message shape, so a bad config fails at `load_config`
    rather than silently looping at runtime.

99. **`GET /kpis`' `spend_vs_budget` becomes `max(pct across every
    configured driver budget, default=0.0)`, not a dict keyed by
    driver.** v4 §2 removed the single global budget number this field
    used to read (`core.runner.budget_state`); nothing in the P5 prompt
    or the plan's KPI list (drift %, prediction-vs-actual touch drift,
    rubber-stamp rate, tokens per node, "spend vs budget per driver")
    asks for `spend_vs_budget` itself to become a per-driver breakdown --
    the plan's own wording splits that out as a *separate* dashboard
    concept ("spend vs budget per driver"), which `core.runner.
    driver_budget_states` already serves for a future dashboard panel.
    Keeping `spend_vs_budget` a single scalar preserves the existing
    `/kpis` response shape (no test asserted its value, only its
    presence) and reads as "the most urgent budget signal right now" --
    the worst-case driver, not an average or a sum across incompatible
    units.

100. **v4 §7 commit-trailer enforcement relocation: removed the
     `PreToolUse` git-commit string-match block outright (not
     scope-narrowed it); generalized the P7 unattributed-commit signal
     as an *additive* second mechanism, not a replacement of the
     existing one; scoped the general check unconditionally (every
     commit whose trailer doesn't resolve), not to commits touching an
     `in_progress` node.** Three separate calls, recorded together:

     - Removal, not narrowing: `core.claude_hooks.pre_tool_use` and
       `muvue._hook._decide`'s `Bash` branch (the only place
       `_is_git_commit`/`_GIT_COMMIT_RE` were used) are deleted, not
       fixed with a better regex -- v4 §7's own framing ("that match is
       defeated by `git -C`, heredocs, chained commands, aliases and
       scripts") is a structural objection to string-matching a shell
       command at all, not a request for a more thorough matcher. `Bash`
       was also dropped from `muvue._hook._BLOCKING_TOOL_NAMES` (it had
       no other decision logic), so a `Bash` `PreToolUse` call no longer
       opens the DB at all -- a direct, in-scope consequence of removing
       the only thing that needed it, not a new optimization.
     - Additive, not a replacement: `core.drift.flag_unattributed_commit`
       (P7 drift loop item 2 -- fires only when the commit's touched
       files overlap an anchored component, event type
       `inbox.unattributed_commit`) is untouched, including its existing
       tests (`test_drift.py`, `test_api_p7.py`). A new function,
       `core.drift.flag_general_unattributed_commit`, covers v4 §7's
       broader ask (every commit, component-anchor-agnostic) as a
       second, independent event type, `unattributed_commit`, both
       called from `core.hooks.handle_post_commit`. Kept separate
       (rather than widening the existing function's firing condition)
       because they answer different questions -- "did this touch
       something we're tracking closely" vs. "does this commit's
       trailer resolve at all" -- and widening the existing one would
       have broken its existing test assertions for no benefit, per
       working rule 8 (no unrequested changes) applied to test coverage,
       not just features.
     - Scope of the general check: v4 §7's prompt offers two readings --
       (a) scope to commits touching an `in_progress` node's
       predicted/actual touches, or (b) any commit at all while some
       node is leased and the commit lacks *that* node's trailer -- but
       also says the post-hoc mechanism has no equivalent to
       `PreToolUse`'s per-tool-call context to scope by, and explicitly
       prefers over-flagging ("erring toward 'flag it, a human can
       dismiss a false positive in the inbox' is safer than silently
       missing real unattributed work"). Chose the simplest reading that
       satisfies the literal ask ("EVERY commit missing a correct
       `Muvue-Node:` trailer... regardless of whether it touched an
       anchored structure component or not"): fire whenever the parsed
       trailer ids (if any) don't resolve to a real, non-deleted node,
       full stop -- no scoping by lease state, no scoping by touched
       paths. `GET /inbox` surfaces it as its own `"unattributed_commits"`
       list, distinct from `"signals"` (still `inbox.unattributed_commit`
       only).

     Coordination note: a parallel session was concurrently adding
     decisions from #99 for v4 §9 (structure-ref commits) in the same
     checkout (no worktree isolation was available for either session);
     this entry is numbered #100 as seen at the time this session wrote
     it, and may need renumbering when both branches merge if the other
     session's entries land at a different number.

101. **v4 §9 structure commits: kept the git plumbing local to
    `core/close.py` (`_write_structure_commit`, `_maybe_fast_forward_main`),
    not factored into `core.gitutil` or a new module.** `core.gitutil`
    is explicitly scoped to the branch-coherence check's single `git
    rev-parse` call (its own docstring: "a small git subprocess helper
    for the branch-coherence check"); `core.strict`/`core.merge` each
    already keep their own local `_run_git*` wrapper for their own
    single call site rather than centralizing, and this session's
    `_write_structure_commit` is close.py's only caller. `current_branch`
    itself *is* reused from `core.gitutil` inside `_maybe_fast_forward_main`,
    per the prompt's explicit instruction to reuse it if suitable.
    (Coordination note: this session and a parallel v4 §7 session
    (trailer-enforcement relocation) shared the same checkout -- no
    worktree isolation was available for either -- and both
    independently numbered their new entries starting at #100. A stray
    `git stash pop` during a branch mixup also briefly carried this
    session's entries onto the §7 branch's commit 56ad367, which was
    cleaned up there. Renumbered to #101-#107 (after §7's own #100) at
    merge time.)

102. **`git merge --ff-only`, not a raw `git update-ref refs/heads/main
    <sha>`, for the fast-forward.** `main` is the branch `repo_root`'s
    own `HEAD` is actually checked out on in the safe case (branch ==
    `main`, clean tree) -- a raw ref move advances the branch pointer
    without touching the index or working-tree files, which would leave
    `git status` reporting every file changed by the structure commit as
    locally modified (a false-dirty tree) until the user next ran
    `checkout`/`reset` themselves. `merge --ff-only` updates `HEAD`, the
    index and the working tree together as one real git operation and
    refuses outright, with no partial effect, if a fast-forward genuinely
    isn't possible -- which doubles as the ancestry check, so no separate
    `git merge-base --is-ancestor` call was added.

103. **Parent/base-tree selection for a `muvue/structure` commit: the
    ref's own current tip if it exists, else the repo's current `HEAD`.**
    v4 §9 says exactly this ("using... whatever `muvue/structure`'s
    current tree looks like... or the repo's current HEAD"), read
    literally as choosing the *commit* (not just its tree) as the new
    commit's parent -- giving `muvue/structure` real, continuous ancestry
    back to a point on the branch that first created it. This is what
    makes `_maybe_fast_forward_main`'s `git merge --ff-only` succeed
    without extra bookkeeping when a project closes on a repo whose
    `main` has already fast-forwarded past a prior structure commit, and
    what makes it correctly *refuse* (falling through to the inbox path)
    when `main` picked up commits of its own since the last close that
    never made it into `muvue/structure`'s lineage.

104. **Single-writer safety on `refs/heads/muvue/structure` itself: a
    compare-and-swap `git update-ref refs/heads/muvue/structure <new>
    <old>`** (old = the exact value `_write_structure_commit` read
    moments earlier, or `""` if the ref didn't exist yet), not a bare
    `git update-ref <ref> <new>`. The prompt only required *confirming*
    the temp-index-per-call design is race-safe and noting it explicitly;
    this is that note plus the concrete mechanism: even if two
    `close_project` calls (or a `close_project` racing a manual `git
    branch -f muvue/structure ...`) raced past the point of reading the
    ref, only the first `update-ref` to land wins and the second fails
    loudly (`CloseError`) instead of silently clobbering the first
    commit off the ref.

105. **No `gh pr create` wiring in this session; the required minimum
    (an unacked `inbox.structure_update_ready` event) is what ships.**
    v4 §9 offers PR-or-inbox and the P6 prompt explicitly marks the PR
    path optional ("this is optional/your call"). `core.github.
    create_pr_via_gh` (built in a prior ad-hoc session, decision-noted
    there) would need a real GitHub remote and an authenticated `gh` --
    neither is guaranteed for an arbitrary repo a `close` runs against,
    and wiring it in means every non-fast-forward `close` either needs a
    `--pr`-style flag (new CLI surface, out of this session's requested
    scope: "no unrequested features") or silently attempts a network call
    on every close. Left for a future session that actually wants
    `close --pr`, matching how `merge --pr` already treats PR creation as
    an opt-in flag rather than automatic (`core/merge.py`, `cli/main.py`).

106. **Test-fixture change: `tests/test_close.py`'s `repo` fixture now
    commits a `.muvue/.gitignore` (ignoring `muvue.db`/`muvue.db-*`/
    `queue.jsonl`) in the repo's *initial* commit, instead of creating a
    bare untracked `.muvue/` directory after that commit.** Needed once
    `_maybe_fast_forward_main` started gating on a genuinely clean `git
    status --porcelain` (v3's commit-straight-onto-`main` implementation
    never checked cleanliness at all): an untracked `.muvue/` directory
    made every fixture repo look dirty from the very first commit, which
    would make the "clean main -> fast-forward" case untestable with this
    fixture. A real `muvue init` already commits `.muvue/config.toml`
    plus a `.gitignore` for exactly this reason (plan §2's file-layout
    table); the fixture now mirrors that instead of only mirroring the
    file layout's tracked/untracked split for `components.json`/
    `decisions.json`.

107. **`tests/test_close.py`'s new working-tree-untouched assertions
    filter `.muvue/history/` out of the `git status --porcelain` they
    compare.** `close_project`'s event-history export (`core/history.py`,
    P6, unrelated to this session's scope) unconditionally writes
    `.muvue/history/<id>.jsonl.gz` on every confirmed close and this
    fixture's `.gitignore` doesn't cover it, so it shows up as a new
    untracked path on *every* scenario, including the two "untouched"
    ones. That's a real, pre-existing gap in P6's history-export/close
    integration (nothing commits or gitignores the history archive it
    writes) -- out of scope to fix here ("smallest correct change"); the
    filter keeps this session's tests asserting only what they're
    actually testing (the structure-ref mechanism), not silently passing
    by asserting something weaker than intended, nor failing on an
    unrelated pre-existing gap.

108. **`uninit` now deletes the `muvue/structure` git ref (`refs/heads/
    muvue/structure`, `core.close.STRUCTURE_REF`) if one exists, skipping
    the delete only if it's the ref currently checked out as `HEAD`.**
    v4's P0 acceptance wording ("no muvue-owned tracked or untracked
    files") predates §9's structure-ref mechanism in the plan's own
    phase ordering (P0 before P6) and never says anything about the
    ref's `uninit` lifecycle either way -- genuinely ambiguous, per
    working rule 7. Judgment call: a `muvue/structure` ref is exactly as
    muvue-owned as `.muvue/` itself (created and only ever written by
    `core.close._write_structure_commit`, via `git update-ref`, never by
    the user), so it falls under the same "no muvue-owned artifacts
    remain" umbrella the tightened P0 wording is really asking about,
    even though the literal sentence only names files. Implemented with
    `git update-ref -d` in `core/repo_init.py`'s new
    `_delete_structure_ref_if_present`, called from `uninit_repo` after
    the manifest restore, before `.muvue/` itself is removed -- never
    touches the working tree, index, or `HEAD` (same guarantee
    `_write_structure_commit` relies on), matching working rule 3 (git
    ref manipulation is not a sqlite write, so it's plain git-subprocess
    work, not `write_txn`-relevant). The "don't delete a currently
    checked-out ref" guard is defensive: no normal workflow reaches it
    (nothing in muvue ever checks the structure ref out as a working
    branch), but it costs one `git symbolic-ref -q HEAD` call to avoid a
    needless footgun if a user manually did `git checkout muvue/structure`
    before running `uninit`.

109. **Real gap found by the new filesystem-snapshot round-trip test, and
    fixed: `.muvue/.init_manifest.json` was never added to the
    marker-block `.gitignore` entries `_update_gitignore` writes.**
    `.init_manifest.json` (the backup file `uninit_repo` replays to
    restore hook shims/`.gitignore` to their pre-`init` contents) must
    stay recoverable purely from the live filesystem for `uninit` to work
    correctly; being un-ignored meant an ordinary mid-project `git add -A
    && git commit` (a completely normal thing for a human or agent to
    do while a muvue project is in progress -- muvue never told them not
    to) would sweep it into history like any other untracked file. A
    later `git reset --hard` (to an earlier commit, or anything else
    that mutates the tracked tree back past that point) would then
    delete or stale `.init_manifest.json`, and `uninit_repo` silently
    treats a missing manifest as "nothing to restore" (see its `if
    manifest_path.exists():` guard) rather than erroring -- so `uninit`
    would quietly leave the original (pre-`init`) hook file contents
    unrestored and report success. Reproduced directly: the new
    `test_init_uninit_filesystem_snapshot_roundtrip` test's usage-
    exercise step (create project/node, commit, run the post-commit
    hook, write a structure commit) does exactly this `git add -A` +
    later `git reset --hard` sequence, and failed on 3 of 4 fixtures
    (`plain_python`, `docs_only`, `monorepo` -- all three have no
    `.husky/`, so `init` writes real `.git/hooks/post-commit`/`pre-push`
    shims that need restoring; `js_husky` failed only on the unrelated
    `.git/ORIG_HEAD` housekeeping artifact below) before this fix landed.
    Fix: added `.muvue/.init_manifest.json` to `_update_gitignore`'s
    entries list in `core/repo_init.py` -- smallest correct change, no
    change to `init_repo`/`uninit_repo`'s own restore logic, since the
    restore logic was already correct given an intact manifest.

110. **`test_init_uninit.py`'s filesystem snapshot excludes `.git/
    ORIG_HEAD` alongside the pre-existing `.git/index`/`objects`/`logs`/
    `COMMIT_EDITMSG` exclusions.** `ORIG_HEAD` is written by `git reset
    --hard` itself (git's own pre-reset-HEAD bookkeeping, restorable via
    `git reset --hard ORIG_HEAD`) -- the new test's usage-exercise helper
    runs `git reset --hard HEAD~1` to undo its own test commit and leave
    the *tracked* tree matching the pre-`init` baseline again, and that
    `reset --hard` is the helper's own action, not something `init`/
    `uninit` do or need to account for. Same category as the other
    git-managed exclusions: something git itself writes as a side effect
    of an ordinary git command, unrelated to what muvue touched.

## v4 delta closure decisions

111. **Daemon reconcile never acks events.** `events.acked_at` means
    "a human acknowledged this" to the inbox. The P2-era `process_queue`
    acked the oldest 100 unacked events on every `serve` start as a
    stand-in for queue processing whose consumers were never built.
    Removed rather than filtered by type: the only real queue is the hook
    spool (`.muvue/queue.jsonl`), and `reconcile_on_start` now drains that
    instead. Only `POST /events/{id}/ack` (and the CLI `ack`) set
    `acked_at`.

112. **Hook spool drain uses a rename hand-off plus a drainer lock.**
    Hooks keep doing a plain `open(..., "a")` append (no locking on the
    hot path, so the section 4a latency budget is unaffected). A drainer
    takes a non-blocking `flock` on `.muvue/queue.lock`, renames
    `queue.jsonl` to `queue.draining`, and processes that. A bounded
    drain leaves its remainder in `queue.draining`, which the next drain
    processes before renaming the live spool again, so order is FIFO.
    Bytes a hook wrote to the old inode after the read are appended to
    the remainder. `fcntl` is POSIX-only, as is the rest of muvue's hook
    and git plumbing.

113. **The airlock tracks the checkout on a side ref.** `ensure_airlock`
    fetches the checkout's `HEAD` into `refs/muvue/upstream`. `main` is
    set from it only when `main` is missing or is an ancestor of it
    (fast-forward). When `main` is ahead (merges) or has diverged, it is
    left alone, because merge commits live only in the airlock until the
    user pulls them. This replaces the forced `+HEAD:refs/heads/main`
    fetch that #49 flagged as a stomping risk but only guarded in the
    merge path.

114. **The Claude Code decision hooks read the DB; supersedes #80.**
    #80 made SessionStart, Stop and PreCompact queue-only to protect the
    section 4a latency budget, which dropped plan section 7's behaviour
    (inject the brief; block unlogged work; require a summary). That
    budget exists because PreToolUse fires per tool call. SessionStart
    fires once per session, and Stop and PreCompact once per turn or
    compaction, so they now use PreToolUse's read-only path under the
    same 150ms fail-open deadline. This narrows section 4a's "only
    PreToolUse reads the DB" to "only decision hooks read the DB, all
    under the deadline". The deadline is now enforced during the query
    (lock-wait timeout plus an sqlite progress handler), not only
    checked afterwards. Other choices:
    - "Unlogged work" means no `note.added` event since the node's
      latest `node.start`, compared by event id because a note and a
      restart can share a millisecond.
    - Claude Code sends no summary in its PreCompact payload, so "require
      a summary" is read as "a progress note since start, or a
      `summary` field if a caller provides one".
    - Stop allows while `stop_hook_active` is true, as the installed CLI's
      own guidance says. The block is a nudge, not a trap.
    - `core.claude_hooks` was deleted. `muvue hook NAME` calls
      `muvue._hook.run`, so there is one implementation.

115. **The daemon drains the hook spool in a lifespan background task.**
    #83 drained inside the SSE generator because no daemon loop existed
    yet, so nothing drained without an open dashboard, and each stream
    stopped after about 60s. `create_app` now starts a task that drains
    every `drain_interval_s` (0.5s) for the app's lifetime. Each drain
    opens its own connection on the worker thread. The SSE loop only
    polls `PRAGMA data_version`.

116. **`deps` and `project_links` have writers.** `create_node(depends_on=
    [...])` (CLI `decompose`/`replan --depends-on`, repeatable) writes
    `deps` edges and a `dep.added` event for each. Dependencies must be
    live nodes in the same project; a new node can't be depended on yet,
    so creation can't form a cycle. `project create --follows/
    --supersedes` writes `project_links` plus a `project.linked` event.
    `component_edges` and `invariants` stay schema-only: plan section 3
    lists the tables but no verb or flow writes them, and adding one
    would be a new, unspecified verb (working rule 8). `external_refs`
    keeps `ext_id` for the spec's `id` column, because `id` is the
    surrogate primary key every table here uses.

117. **The v3 per-project budget columns are dropped** (schema 6).
    `projects.budget_unit/budget_limit/spent` and `project create
    --budget-unit/--budget-limit` contradicted v4's "no single budget"
    rule, and nothing enforced them. `migrate` drops the columns with
    `ALTER TABLE ... DROP COLUMN` (sqlite >= 3.35). Replay ignores the
    keys in old `project.created` payloads.

118. **Lessons are validated at the write path.** Plan section 3:
    "lessons must carry trigger, failure, do_instead, scope." `add_note
    (kind="lesson")` requires the JSON form `nodes.lesson_text` builds,
    with all four fields non-empty, and `fail` builds it from `--lesson`
    (the failure) plus the required `--trigger`, `--do-instead` and
    `--scope` (the same fields in MCP and API). The fake agent's "lazy
    vacuous lesson" behaviour is now refused, and the node's attempt is
    not consumed. Lesson *quality* is still not judged.

119. **Replay covers the whole section 3 replayable list; supersedes
    #78.** The fold adds `deps` (`dep.added`), `node_commits` and
    `actual_touches` (`commit.linked`, first link wins as on the write
    side), plan-revision approvals (`revision.proposed`/
    `revision.approved`) and `agent_spend` (summed `spend.recorded`).
    Approvals compare as approved-or-not, since `approved_at` is
    wall-clock. `rebuild --apply` backs the DB up with sqlite's online
    backup API, then rewrites those tables from the log in one write
    transaction and rebuilds the FTS indexes. `nodes.lease_until` comes
    from the last snapshot. `notes.last_retrieved_at` keeps its live
    value. Tables outside the replayable set (questions, predicted
    touches, usage, decisions, external refs) are left untouched.

120. **Every read goes through `read_txn`; supersedes #70.** Principle 2
    ("no raw `conn.execute` outside them") is now enforced by
    `tests/test_txn_discipline.py`, an AST check over the whole package.
    Exempt: `core/db.py`, `core/migrate.py`, `muvue/_hook.py` and
    `PRAGMA` statements. Single-statement reads use `db.query_one`/
    `db.query_all`, which run inside `read_txn`. `read_txn` now raises
    if a write happened inside it, because its closing rollback would
    otherwise discard the write silently.

121. **`export` writes the section 2 layout; supersedes #6.** Without
    `--project-id` it writes every project's `.jsonl.gz` archive plus
    `unscoped.jsonl.gz`, instead of a flat `events.json`.

122. **`pause` is an emergency stop for running agents.** Every `muvue
    run` process registers itself in `.muvue/runners/<pid>.json` and
    handles SIGTERM. `pause` sends SIGTERM to the project's runners and
    waits up to 15s for their registry files to disappear. It checks the
    registry file rather than the PID, because a zombie runner still
    has a PID. The runner kills each driver's process group (drivers
    run under `start_new_session=True`) and releases its leases to
    `ready` without using up an attempt: the agent didn't fail, a human
    stopped it. A runner that finds its project paused between nodes
    also stops, with reason `project_paused`.

123. **Invoker detection is implemented; supersedes #73.** v4 section 4
    names `actor_evidence` as the compensating control for agents
    calling human verbs. `core.actor.detect_invoker` walks the parent
    process chain through `/proc/<pid>/stat` and `cmdline`, falling
    back to `ps` where there is no `/proc`. An ancestor whose command
    name or argv[0] is a known agent CLI (`claude`, `codex`, `gemini`,
    `cursor-agent`, `aider`) makes the call `actor=agent`,
    `actor_evidence=agent_parent:<name>`. `require_human` still allows
    that pairing, because v4 principle 10 asks for detection, not
    prevention: an agent can get a TTY, so blocking here would only
    look like safety. Otherwise the evidence is `tty` or `no_tty`. The
    API records `dashboard_token` and MCP records `mcp`, as before.

124. **Request-id for verbs that have no event of their own.** `start`,
    `done`, `fail` and `ask` already dedupe on their own events. Every
    other mutating verb goes through `core.idempotency.once`, which
    records a `request.<verb>.completed` event carrying the result and
    replays that result for a duplicate within 24h. Verbs that do slow
    work outside the DB (merge, close, import, pause) check and record
    in separate transactions, so the write lock isn't held across git
    or process waits. Two concurrent duplicates of those verbs can
    therefore both run, and each verb's own idempotency covers that
    race. The API reads the id from the `X-Request-Id` header, so a
    body-less POST can carry one.

125. **`ask` takes the default policy at ask time.** v4 section 4 puts
    `--default-ok` on `ask`, not `wait`. It is stored as
    `questions.default_ok` (schema 7). `wait` applies the default on
    timeout if either the question or the `wait` call says so. `wait
    --default-ok` is kept for compatibility. `--default` is required,
    because an unanswered question with no proposal can only block.

126. **Criteria edits after `start` park the node.** Before this,
    `edit_criteria` refused an `in_progress` node, so `awaiting_approval`
    was unreachable and the PreToolUse check for it was dead code. A
    changed criteria hash on an `in_progress` node now moves it to
    `awaiting_approval`, keeping the owner and lease, with the tier
    forced to `high`. `approve_node` accepts `awaiting_approval`,
    refreezes the hash, and moves the node back to `in_progress`. An
    edit that leaves the hash unchanged only bumps `version`, as
    before.

127. **Diff size and deletions come from git; supersedes #18 and #19.**
    `node_commits` has recorded each node's commit SHAs since P3, so the
    real diff is available. `core.nodes.done` runs `git show --numstat`
    and `git show --diff-filter=D --name-only` over those SHAs before
    its write transaction, in the checkout (light) or the node's
    worktree (strict). The line total is compared against
    `risk.max_diff_lines`, and any deleted file forces `high`. The file
    count against `max_files_per_task` uses committed paths. If git
    can't read the commits, or the node has none, the touch-count
    proxy from #18 still applies. Binary files count as zero lines.

128. **`auto` criteria always run; supersedes #38.** #38 made checks
    opt-in because turning them on changed P0-P2 behaviour. v4 section
    5 says "muvue runs `auto` criteria itself", and an opt-in flag that
    an agent can simply omit makes `auto` criteria self-attested. The
    CLI, MCP, API and runner now always pass `default_run_checks`, and
    `[checks] lint` runs after `test`. A missing tool (for example no
    `ruff` installed) fails the check and flags the node for review.
    That is noisy, but it is honest, and it is fixed in `config.toml`.
    Core `done(run_checks=None)` remains for unit tests of the tier
    gate. Checks run before the write transaction so a long suite
    doesn't block every other writer.

129. **Rubber-stamp is a medium/high metric across all approvals.** v4
    section 5: "Time-to-approve under 10 s on a medium/high node is
    logged as a rubber-stamp signal." The old code timed only
    `approve_review`, for every tier, and divided by all `node.done`
    events, which included auto-approvals. `nodes.log_approval_timing`
    now times `approve_review` from `node.review`, and `approve_node`
    (Gate 2, revisions, re-approval) from the latest `node.created`,
    `node.pending` or `node.awaiting_approval`. It records
    `metric.approval_timed` for every medium/high approval and
    `metric.rubber_stamp` for the fast ones. The KPI is the ratio of
    the two.

130. **`replan` scope is enforced by globs and count.** Section 6:
    "`replan` may add subtasks within an approved task's stated scope
    without approval." The stated scope is read as the parent's
    `predicted_touches`: a subtask glob is inside when `fnmatch` matches
    it against a parent glob. That is a string-level check, so
    `src/**` under a parent `src/*` passes, because `*` in `fnmatch`
    crosses `/`. A subtask with no predicted touches inherits the
    parent's scope. Out-of-scope subtasks, or ones past `max_subtasks`,
    are created `pending` rather than refused, so the agent's plan is
    kept and a human decides.

131. **The brief's line grammar and its choices.** v4 section 4 gives
    one example line (`T3.2 in_progress "..." deps:T3.1 touches:...`)
    and a ranking rule. What was chosen where the spec is silent:
    - **Node ids.** They are `T<id>` for every kind: ids are flat
      integers here, with no `3.2` hierarchy.
    - **Quoting.** Strings are JSON-quoted so a line can be parsed
      without ambiguity.
    - **Always printed.** The header, node line, criteria and open
      questions print even when they alone exceed the budget. An agent
      that can't see its criteria can't finish the node.
    - **Cutting.** Everything else is cut at the first line that
      doesn't fit, not bin-packed, so the priority order holds.
    - **Lesson scope.** Scope is matched by node id, project id, `*`, or
      a glob overlapping the node's touches. Free-text scopes such as
      "pricing parser" only reach their own node, because matching
      words would be guesswork.
    - **Lesson decay.** A retrieval is recorded only for lessons the
      brief actually printed.
    - **SessionStart.** The hook keeps its own short summary. The full
      renderer needs `muvue.core`, which `muvue._hook` may not import
      (section 4a).

132. **Light mode can isolate nodes; merges go into the user's checkout,
    carefully.** v4 section 6 allows `--parallel` only under
    `worktree_mode = "per_node"`, but light mode used to ignore the
    setting, so parallel agents still shared one checkout. Light
    `per_node` now makes a worktree of the user's own repository (no
    airlock) on `node-<id>` off HEAD. Light mode's trust model is "the
    user's repo is the truth", so the branch merges into the checkout
    itself, with `git merge --no-ff`. It merges only when there are no
    uncommitted changes to tracked files, because merging over someone's
    edit in progress would be worse than waiting. The runner also stops
    scheduling a node before its `deps` are done, so a dependent node
    branches off its dependency's merged work.

133. **`wait` sleeps inside the run.** Before this, `on_rate_limit =
    "wait"` blocked the node and ended the run, so waiting only
    happened if someone ran `muvue run` again. The runner now sleeps
    until `retry_at`, capped at the remaining `max_wait_minutes`, and
    retries the node in the same run. The sleep is an `Event.wait`, so
    `pause` and SIGTERM end it at once. Tests inject a clock whose
    `sleep` advances `now`.

134. **Notifications are sent; supersedes #97.** `core.notify` POSTs to
    `[notify] url` with stdlib `urllib`, so there is no new dependency.
    - **Format.** Plain-text lines, because an ntfy topic, the spec's
      own example, displays text as-is and a webhook can still use it.
    - **At most once.** A failed POST is recorded as `notify.failed`
      and not retried. Otherwise an endpoint that is down would be hit
      on every daemon tick.
    - **No history replay.** The first flush starts at the end of the
      log.
    - **Idle flushes write nothing.** Recording "nothing new" would
      itself be a new event on every tick.

135. **`start?agent=X` spawns a runner; resolves the #84/threat-model
    contradiction.** The threat model has always described this
    endpoint as launching an agent, while the code only recorded the
    request. It now launches `muvue run --node ID --agent X` detached
    (`start_new_session`), and the runner takes the lease itself as
    `runner:X`. Because the runner registers in `.muvue/runners/`,
    `pause` stops it like any other run. The agent name must be a
    configured `[agents.X]`: the endpoint never runs an arbitrary
    command, only what `config.toml` already allows.

136. **`close` proposes anchored and changed components; supersedes
    #55.** #55 used `predicted_touches` globs as the only candidate
    source, because nothing recorded what a node actually changed and
    nothing called `create_anchored_component`. Both reasons are gone:
    `actual_touches` is filled from each node's commits, and anchoring
    is cheap (`git show HEAD:<path>`). So `close` now proposes one new
    component per actually-touched file that no component anchors yet,
    and confirming creates it through `drift.create_anchored_component`
    at HEAD. It also lists existing components whose anchors the project
    touched (`changed_components`); confirming re-verifies them at HEAD.
    A project with no recorded commits still falls back to its globs,
    unanchored, so planning-only projects close as before.

137. **Lesson decay counts later projects, not retrievals across all
    projects.** The old rule archived any lesson retrieved in fewer than
    K distinct projects, which archived every new lesson on the first
    `audit`, before anyone could use it. v4 §9 says "not retrieved in K
    projects". This is now read as: the K most recent projects started
    after the lesson's own project all passed without a brief retrieving
    it. A lesson younger than K projects is kept. Pinned lessons are
    never archived.

138. **Reconcile-on-touch uses actual touches, and `done` re-verifies;
    supersedes #61.** #61 matched stale components against
    `predicted_touches` only, because no real per-node touch data
    existed. `actual_touches` now exists, so the check unions predicted
    and actual paths: an agent that edits a stale component's file
    without predicting it is still caught. The loop also closes now.
    Approving a review re-verifies the stale components the node touched
    (new anchors hashed at HEAD, `status='current'`, new
    `verified_sha`), because the human just reviewed that code. The
    Claude Code `Stop` hook blocks ending a turn on an `in_progress` node
    that touches a stale component, unless a note since `start` mentions
    it (`C<id>`). This uses the same bounded read path as #114.

139. **`audit` items carry a real draft.** Each `inbox.audit_drift_signal`
    payload now has `diff` (the anchor files' `git diff` since
    `verified_sha`, capped at 200 lines) and `proposed` (the anchors and
    `verified_sha` the component would get if accepted). The
    CHANGELOG had claimed this since P7. Now it is true.

140. **`close --pr` opens a pull request; supersedes #105.** #105 left PR
    creation out because it needs a GitHub remote and an authenticated
    `gh`, and the plan marks it optional. It stays opt-in: `close --pr`
    (and `"pr": true` on the API) runs `gh pr create` against the
    `muvue/structure` branch only when `main` could not be
    fast-forwarded. The origin must be a GitHub URL. Any failure (no
    GitHub origin, `gh` missing or logged out, push refused) is recorded
    as `pr_error` on the result and the inbox item, which is still
    created. A plain `close` never makes a network call.

141. **`.pre-commit-config.yaml` registration, text-only; supersedes
    #34.** #34 skipped this because safe YAML rewriting needs a parser.
    It can be done without one in the common case. When `repos:` is a
    block list and the last top-level key, appending a
    marker-delimited `- repo: local` entry at the list's own indent
    cannot change the meaning of anything above it. Any other layout
    (flow list, a key after `repos:`, no `repos:`) is left untouched, as
    before, and the `.git/hooks` shim still runs. `uninit` restores the
    original bytes from the manifest, like every other file `init`
    touches.

142. **Strict `pre-push` check and a stdlib `pre-receive`; completes
    #4.** #4 named `pre-push` as the strict-mode check, but it only
    spooled an event. In strict mode, `pre-push` now refuses a push
    whose commits carry a `Muvue-Node:`/`Refs:` trailer naming a node
    that isn't `done`: that work skipped review and the airlock. Light
    mode still only spools. Trailers are labels (plan §5), so this
    catches accidents, not someone who drops the trailer; the airlock's
    `pre-receive` is still the real barrier. The airlock shim is now the
    one-line `-S -m muvue._hook pre-receive` fast path instead of the
    full CLI, so each push no longer pays for importing Typer and
    Pydantic. `strict.handle_pre_receive` delegates to the same code.
    `pre-receive` fails closed: outside an airlock, or with an
    unreadable DB, it refuses.

143. **Every mutating request must be JSON, body-less ones included.**
    Control 4 used to exempt a POST without a body on the theory that it
    had nothing to smuggle. But a body-less verb such as `pause` or
    `ack` still acts, and an empty cross-site form POST to it carries
    the session cookie wherever SameSite doesn't apply (an older
    browser, or a same-site origin on another port). Requiring
    `Content-Type: application/json` everywhere means a form can never
    reach a mutating route, and a cross-site `fetch` with that header
    needs a preflight this app never answers. The dashboard and the VS
    Code extension already sent the header.

144. **The dashboard link carries a one-time nonce, not the token;
    embedded dashboards use an in-memory header token.** v4 §8a says the
    URL fragment is "one-time", but it used to be the token itself, so a
    link left in browser history, a screenshot or a terminal scrollback
    was a working credential until `serve` restarted. `serve` now mints
    a single-use nonce for the link and prints the token separately for
    API clients. `POST /auth/nonce` mints more nonces for a caller that
    holds the token.

    Inside the VS Code webview (the iframe from #67) the dashboard is a
    cross-site subframe, and a SameSite=Strict cookie is never sent
    there, so the cookie exchange silently left it read-only. When the
    page detects it is framed, it asks the exchange for the token
    (`"header": true`) and keeps it in a JS variable. The extension also
    stops storing the token in `SecretStorage`: the token rotates on
    every `serve` restart, so a stored copy only goes stale, and v4 says
    nothing token-shaped goes on disk. A 403 makes the extension forget
    the token and ask again. #67's iframe itself stays.

145. **Spec line comments are `[L<n>] `-prefixed feedback notes.** The
    plan stores spec comments as `feedback`. Adding a line column would
    need a schema change for one integer, and the agent reads notes as
    text in its brief anyway. So the anchor is part of the text, where
    the agent sees which line the comment is about. The API validates
    that the line exists in the node's body; the dashboard renders the
    comment under that line.

146. **`doctor` probes the bind (control 1).** It used to skip control 1,
    saying it couldn't observe how another process was started. It can
    observe the result: `probe_bind` connects to the daemon's port on
    each non-loopback IPv4 address of this machine (the outbound route's
    address and the hostname's addresses, found without sending a
    packet). This runs before the loopback reachability check, because a
    daemon bound only to an external address is invisible on 127.0.0.1.
    Addresses the stdlib can't discover (an interface with no route and
    no hostname entry) are not probed.

147. **Per-driver spend is on `/kpis`; supersedes #99.** #99 kept
    `spend_vs_budget` a single number because nothing asked for a
    breakdown, but v4 §8 lists "spend vs budget per driver" as a KPI.
    `spend_by_driver` now carries each budgeted driver's
    `driver_budget_state`; `spend_vs_budget` stays as the worst driver
    for existing clients. `/kpis` also gains `touch_drift` (the §8
    "prediction-vs-actual touch drift") and the rubber-stamp counts
    behind the rate.
