# Vendor CLI sample output (SYNTHETIC / UNVERIFIED)

Every `.jsonl` file in this directory is **hand-constructed, not captured
from a real vendor CLI invocation**. This environment has no network
access and no logged-in `claude`/`codex`/`gemini` CLI, so none of P5's
`core.drivers` usage parsers (`claude_stream_json`, `codex_json`,
`gemini_json`) could be tested against real recorded output, per plan
section 10's "Driver parser tests against recorded CLI output samples for
each vendor."

These samples instead reconstruct each vendor's *documented* output
convention as best effort, from memory, at the time of writing
(2026-09-24) -- the same caveat P3's `docs/providers.md` already carries
for the adapter config writers. **Treat every file here as needing a
human to verify against a real install before relying on the
corresponding parser in production** (see `docs/providers.md`'s P5
section and the P5 handoff report).

- `claude_stream_json_success.jsonl` / `claude_stream_json_rate_limited.jsonl`
  -- `claude -p --output-format stream-json`-shaped: one JSON object per
  line, terminal `{"type": "result", ...}`.
- `codex_json_success.jsonl` / `codex_json_error.jsonl` -- `codex exec
  --json`-shaped: one JSON object per line, terminal `{"type":
  "task_complete", ...}` or `{"type": "error", ...}`.
- `gemini_json_success.jsonl` / `gemini_json_error.jsonl` -- a best-effort
  guess at a single terminal JSON object, using the Gemini API's own
  documented `usageMetadata` field names (`promptTokenCount`,
  `candidatesTokenCount`) as the closest available reference.
