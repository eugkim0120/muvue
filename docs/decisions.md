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
