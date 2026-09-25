# Provider notes

How muvue drives each vendor CLI, and how sure we are. Each section says
what was checked against an installed CLI (with the version and date)
and what is still from memory. This machine has no web access, so
nothing here was checked against vendor documentation online.

## Checking a driver

`muvue doctor` runs every driver's `auth_check`. A non-zero exit is
reported as "not installed, logged out or session expired". `doctor`
also compares the first dotted version number the check prints against
`pinned_version` (for example `">=2.0"` or `">=2.0,<3"`) and warns when
it falls outside, because the output formats muvue parses change
between vendor releases. Pick an `auth_check` that fails when logged
out, not just `--version`, where the CLI has one (see Codex below).

## Claude Code (verified 2026-09-25 against claude 2.1.281)

`muvue adapter install claude-code` merges a `hooks` section into
`.claude/settings.json`. It wires `SessionStart`, `PreToolUse`,
`PreCompact` and `Stop` to the fast-path shim `PYTHONPATH=<dir>
<abs-python> -S -m muvue._hook <event>` (plan v4 section 4a). The hook
contract below was checked against the strings in the installed
binary, not only against docs:

- Exit 2 blocks, and stderr becomes the reason shown to the model. This
  holds for PreToolUse, Stop and PreCompact ("Compaction blocked by
  PreCompact hook").
- On exit 0, SessionStart's stdout is added to the session as context.
- Stop hooks receive `stop_hook_active`. muvue allows the stop when it
  is set, so a blocked Stop can't loop.

### Headless (`muvue run`), run live on 2026-09-25

Six nodes ran through `muvue run` against a subscription-authenticated
claude 2.1.281 (model `claude-sonnet-5`), in a throwaway repository.
This config worked:

```toml
[agents.claude]
command = "claude -p --output-format stream-json --verbose --model sonnet --permission-mode acceptEdits --allowedTools 'Read Edit Write Glob Grep Bash(git add *) Bash(git commit *) Bash(git status*) Bash(git diff*) Bash(git log*) Bash(/abs/venv/bin/python -m pytest*)'"
auth_check = "claude --version && claude auth status"
usage_parser = "claude_stream_json"
cost_model = "usd"
pinned_version = ">=2.1"

[agents.claude.budget]
unit = "usd"
limit = 8.0
```

What the run showed:

- The prompt is read from stdin. `acceptEdits` plus `--allowedTools`
  is enough for edits and commits without an approver. A `Bash` call
  that doesn't match an allowed pattern is denied, not queued, and is
  listed in the result's `permission_denials`. Compound commands
  (`a; b`, `a && b`, pipes) count as one command and are usually
  denied. The agent then retries the parts one at a time.
- `muvue run` tells the agent the `[checks]` commands it will run at
  `done`. Before that, the agent guessed `python -m pytest`, which the
  allow-list didn't cover.
- `--bare` would skip the user's hooks, but it limits auth to
  `ANTHROPIC_API_KEY`, so a subscription login can't use it. Headless
  sessions therefore run the user's own Claude Code hooks, and their
  output appears in stream-json as `system/hook_*` events. That output
  lands in `.muvue/logs/<node>.log` (gitignored). Don't share those
  logs without reading them.
- `claude auth status` prints JSON with no version, and it exited 0
  while logged in. Chaining `claude --version` first lets `doctor`
  check `pinned_version`. The logged-out exit code was not checked,
  because logging out would have ended the run.
- Every result reports `total_cost_usd` even under a subscription
  (`modelUsage.*.costBasis` is `"list"`). A `usd` budget is therefore a
  list-price budget. Six nodes cost $0.98 at list price.
- Cost per node was mostly prompt caching. Each session processed about
  220k to 400k prompt tokens, and nearly all of them were cache reads
  or writes (`usage.input_tokens` was 2 to 18).
- `muvue pause` during a session ended the claude process and the
  runner. The node went back to `ready` with `attempts` unchanged. The
  session's uncommitted edits stayed in the checkout (branch mode), and
  the next session found and committed them.
- No rate limit was hit. Each session emits `rate_limit_event` with
  `rate_limit_info.status` (`"allowed"` throughout) and `resetsAt`; the
  parser treats an error result under a non-`allowed` status as a rate
  limit and waits until `resetsAt`. That path is tested on the recorded
  sample with the status changed, not on a real limit.

Terms for unattended use under a subscription change. Check them before
relying on unattended runs.

## Codex (verified 2026-09-25 against codex-cli 0.142.5)

- `codex exec [PROMPT]` is the non-interactive mode. With no prompt
  argument, or `-`, it reads instructions from stdin, which is how
  `muvue run` passes the brief.
- `--json` prints events to stdout as JSONL.
- `-s/--sandbox` takes `read-only`, `workspace-write` or
  `danger-full-access`. `workspace-write` is the one that lets it edit
  the node's worktree.
- `-C/--cd DIR` sets the working directory. muvue already spawns the
  driver in the node's checkout.
- `codex login status` exits 1 when not logged in, and `codex
  --version` exits 0 either way. Use `auth_check = "codex login status"`
  so `doctor` and the runner catch a logged-out Codex.
- `muvue adapter install codex` appends a muvue section to `AGENTS.md`,
  which Codex reads as project instructions. Codex also has hook
  configuration (`--dangerously-bypass-hook-trust` exists). muvue
  doesn't install Codex hooks, so under Codex, commits are attributed
  by trailer only.
- The `codex_json` usage parser was written before this check. The
  JSONL event names it expects have not been compared against a real
  `codex exec --json` run here, because this machine's Codex is logged
  out.

## Gemini (not verified: CLI not installed here, 2026-09-25)

`muvue adapter install gemini` writes `GEMINI.md` at the repo root, the
Gemini CLI's project-context file as of the adapter's writing. Headless
use would be `gemini -p` with the prompt on stdin. Confirm the flag,
the output format and a logged-out exit code before routing work to it.

## Cursor (not verified: `cursor-agent` not installed here, 2026-09-25)

`muvue adapter install cursor` writes `.cursor/rules/muvue.mdc` in
Cursor's `.mdc` project-rules format (`alwaysApply: true`). Cursor's
headless CLI is `cursor-agent`. Its print mode, output format and auth
status command are unverified here. Cursor's rules format has changed
before.

## Driver usage parsers (P5, 2026-09-24)

`core.drivers` implements `usage_parser` dispatch for the example
commands plan section 2 shows (`claude -p --output-format stream-json`,
`codex exec --json`) plus a best-effort `gemini_json`.

- `claude_stream_json` is **verified against claude 2.1.281**
  (2026-09-25). A sanitized recording of a real session is
  `tests/fixtures/vendor_samples/claude_stream_json_live_2_1_281.jsonl`.
  The recording showed the first version was wrong in two ways: the
  model is not on the `result` event (it is on `system/init` and in
  `modelUsage`), and `usage.input_tokens` excludes the cached prompt
  tokens that make up nearly all of it. Both are fixed (decision #151).

The other two are **synthetic and unverified**. Codex here is logged
out and Gemini isn't installed. Each was reconstructed from that
vendor's *documented* output convention at the time of writing, not a
captured invocation:

- `codex_json` — one JSON object per line, a terminal `{"type":
  "task_complete", "usage": {"input_tokens": ..., "output_tokens":
  ...}}` on success or `{"type": "error", "message": ...}` on failure.
- `gemini_json` — a best-effort guess at a single terminal JSON object
  using the Gemini *API's* own documented `usageMetadata` field names
  (`promptTokenCount`, `candidatesTokenCount`) as the closest available
  reference, since no CLI-specific JSON output format could be consulted.

Synthetic sample files live in `tests/fixtures/vendor_samples/` (labeled
the same way there) and are what `tests/test_drivers.py` tests these
parsers against. **A human must verify the Codex and Gemini parsers
against a real, logged-in vendor CLI invocation before relying on them
in production** —
the rate-limit-detection heuristic in particular (a loose, case-
insensitive "rate limit"/"429"/"too many requests" text match, since no
vendor's real error text or exit-code convention could be confirmed here)
is the part most likely to need adjustment once real output is available.

`config.agents.fake` (`muvue-fake-agent`, `src/muvue/fake_agent.py`)
stands in for a vendor CLI in the test suite. The P5 criteria that
need a real CLI were run against claude, as described above.

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
