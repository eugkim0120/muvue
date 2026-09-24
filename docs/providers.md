# Provider notes

Populated in P3, when adapters first exist (`muvue adapter install
<name>`, `src/muvue/core/adapters.py`). Every note below is **best-effort
and unverified against live vendor docs** -- this environment has no
network access, so these formats were written from memory of each
vendor's documented conventions at the time of writing, not confirmed
against a real install. Treat every one of these as needing a human to
verify before v0.1 truly ships (see the P3 handoff report).

## Claude Code (2026-09-24)

`muvue adapter install claude-code` merges a `hooks` section into
`.claude/settings.json`, wiring `SessionStart`/`PreToolUse`/
`PreCompact`/`Stop` to `<abs-python> -m muvue hook <event>`. The JSON
shape used (`{"hooks": {"SessionStart": [{"hooks": [{"type": "command",
"command": "..."}]}]}, ...}`, `PreToolUse` carrying a `matcher`) and the
hook stdin/stdout contract (`{"decision": "allow"|"block", "reason":
...}`, exit 2 on block) are reproduced from memory of Claude Code's
documented hooks configuration and hook I/O contract, not verified here.
Headless/subscription use: no specific residual-risk note beyond the
plan's own ("Vendor terms for headless subscription use change") --
verify Claude Code's current terms permit this kind of automated,
repeated hook invocation under a subscription plan before relying on it
for unattended runs.

## Codex (2026-09-24)

`muvue adapter install codex` appends a muvue section to `AGENTS.md` at
the repo root, on the assumption that Codex CLI reads project
instructions from that file (a convention shared with several other
coding agents, including Claude Code's own `CLAUDE.md`/`AGENTS.md`
support). Not verified against Codex's current documented config
surface -- Codex may also support hook-style configuration analogous to
Claude Code's, which this adapter does not attempt.

## Gemini (2026-09-24)

`muvue adapter install gemini` writes `GEMINI.md` at the repo root, the
Gemini CLI's documented project-context file at the time of writing.
Not verified.

## Cursor (2026-09-24)

`muvue adapter install cursor` writes `.cursor/rules/muvue.mdc`, using
Cursor's `.mdc` project-rules format (`alwaysApply: true` frontmatter).
Not verified against a live Cursor install; Cursor's rules format has
changed before and may have again.

## Driver usage parsers (P5, 2026-09-24)

`core.drivers` implements `usage_parser` dispatch for the example
commands plan section 2 shows (`claude -p --output-format stream-json`,
`codex exec --json`) plus a best-effort `gemini_json`. **All three are
synthetic and unverified against a real vendor CLI** — same constraint as
above: no network access, no logged-in `claude`/`codex`/`gemini` CLI in
this environment. Each parser was reconstructed from that vendor's
*documented* output convention at the time of writing, not a captured
real invocation:

- `claude_stream_json` — one JSON object per line, a terminal `{"type":
  "result", "is_error": ..., "result": ..., "usage": {"input_tokens":
  ..., "output_tokens": ...}, "total_cost_usd": ...}`.
- `codex_json` — one JSON object per line, a terminal `{"type":
  "task_complete", "usage": {"input_tokens": ..., "output_tokens":
  ...}}` on success or `{"type": "error", "message": ...}` on failure.
- `gemini_json` — a best-effort guess at a single terminal JSON object
  using the Gemini *API's* own documented `usageMetadata` field names
  (`promptTokenCount`, `candidatesTokenCount`) as the closest available
  reference, since no CLI-specific JSON output format could be consulted.

Synthetic sample files live in `tests/fixtures/vendor_samples/` (labeled
the same way there) and are what `tests/test_drivers.py` tests these
parsers against. **A human must verify all three against a real,
logged-in vendor CLI invocation before relying on them in production** —
the rate-limit-detection heuristic in particular (a loose, case-
insensitive "rate limit"/"429"/"too many requests" text match, since no
vendor's real error text or exit-code convention could be confirmed here)
is the part most likely to need adjustment once real output is available.

`config.agents.fake` (`muvue-fake-agent`, `src/muvue/fake_agent.py`) is
the one driver exercised against a real subprocess in this environment,
and is what P5's acceptance criteria substitute for a real
subscription-authenticated CLI throughout.

## Per-driver budget unit (v4 section 2, this vendor-agnostic)

Each `[agents.<x>]` may now carry its own `[agents.<x>.budget]`
(`unit`/`limit`), separate from the global `[budget]` (which holds only
unit-free stop conditions, `max_wall_clock_minutes`/`max_nodes_per_run`).
The valid `unit` for a given vendor's `cost_model` is fixed by what that
vendor's own usage output can actually produce, not a free choice:
`cost_model = "usd"` -> `budget.unit = "usd"`, `"tokens"` -> `"tokens"`,
`"quota"` -> `"requests"`. This is a config-shape change only; it does
not alter any of the synthetic `usage_parser` guidance above, and
`doctor` rejects a mismatched pairing before a run ever starts. Example
(v4 section 2's own `config.toml`): `[agents.claude]` with
`cost_model = "quota"` pairs with `[agents.claude.budget] unit =
"requests"`; `[agents.codex]` with `cost_model = "usd"` pairs with
`unit = "usd"`.

`max_wait_minutes`/`on_rate_limit_timeout` (v4 section 6, changelog item
11) are also per-driver now, alongside `on_rate_limit` — a vendor whose
rate-limit window is long (e.g. a 5-hour subscription reset) should set
`max_wait_minutes` well under that window so `wait` never turns into an
unbounded stall; `on_rate_limit_timeout` then decides what happens next
(`fallback:<agent>` or `pause`, never `wait` again).

## Residual risk (plan section 6, item 3)

"Vendor terms for headless subscription use change" -- unchanged from
the P0 stub's framing. P3 adds the adapters that make headless use
concrete (MCP server, Claude Code hooks), which makes this risk more
immediate, not less: before running any of these adapters against a
real subscription-tier agent in an unattended loop, re-verify that
vendor's current terms permit it.
