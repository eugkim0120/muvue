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
