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
