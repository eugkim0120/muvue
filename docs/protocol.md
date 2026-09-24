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

Stubs (print `not implemented in P0`): `brief`, `show`, `note`, `ask`,
`wait`, `replan`, `status`.

### Human verbs (stubs in P0; never exposed over MCP)
`approve`, `reject`, `ack`, `merge`, `close`, `pause`, `resume`,
`handoff`, `import`.

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
