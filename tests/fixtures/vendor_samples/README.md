# Vendor CLI sample output

`claude_stream_json_live_2_1_281.jsonl` is **recorded** from a real
`claude -p --output-format stream-json --verbose` session (claude
2.1.281, model `claude-sonnet-5`, 2026-09-25) during the live P5 run. It
was sanitized before being committed:
- `system/hook_*` events were removed, because they carry the local
  user's own hook output;
- the `system/init` event was cut down to its model and version fields;
- `session_id`, `uuid` and `request_id` were removed;
- thinking text and signatures were blanked;
- local paths were replaced with `/repo`.

Every other `.jsonl` file here is **hand-constructed, not captured from a
real vendor CLI invocation**. When they were written, this environment
had no logged-in vendor CLI. Codex is still logged out and Gemini isn't
installed, so the `codex_json` and `gemini_json` parsers have not been
tested against real recorded output, as plan section 10 asks ("Driver
parser tests against recorded CLI output samples for each vendor").

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
