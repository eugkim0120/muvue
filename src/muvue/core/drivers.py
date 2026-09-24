"""Driver invocation layer (plan section 6 "Subscription and API agents",
P5): given an `[agents.<name>]` config entry, spawn `command` as a real
subprocess with `brief` on stdin, run `auth_check` first, and parse usage
from stdout via `usage_parser`.

Drivers are config, not code (plan section 2): this module never hardcodes
a vendor CLI path or flag, only the *shape* it dispatches on
(`usage_parser` names). Muvue never holds agent credentials (plan section
1, principle 7) -- nothing here reads, stores, or forwards an
authentication token or API key; it shells out to whatever CLI
`config.agents.<name>.command` names and lets that CLI's own login
persist in its own state, entirely outside muvue.

Vendor usage-parser shapes (`claude_stream_json`, `codex_json`,
`gemini_json`) are reconstructed from each vendor's documented output
conventions, **not verified against a real install** -- this environment
has no network access and no logged-in vendor CLI (same caveat P3's
adapters.py/docs/providers.md already carry). See
`tests/fixtures/vendor_samples/` for the synthetic recorded-output samples
these parsers are tested against, and `docs/providers.md` for the same
disclaimer in prose. `fake` is the one parser exercised against a real,
muvue-owned subprocess (`muvue-fake-agent`, `src/muvue/fake_agent.py`) --
see P5's acceptance criterion 1.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import AgentConfig

DEFAULT_TIMEOUT_SECONDS = 600

# Loose, case-insensitive signal a vendor CLI's own error text is a rate
# limit rather than some other failure -- there is no standardized exit
# code across vendors to key off instead, so this is the documented,
# testable signal shape P5's prompt asks for (see docs/providers.md).
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "too many requests", "429")


@dataclass
class DriverResult:
    status: str  # "done" | "failed" | "rate_limited" | "unavailable"
    summary: str = ""
    in_tokens: int = 0
    out_tokens: int = 0
    requests: int = 1
    cost: float = 0.0
    model: str | None = None
    retry_after_seconds: int | None = None
    error: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""


def _is_rate_limit_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _json_lines(stdout: str) -> list[dict]:
    """Parse whatever JSON objects can be parsed, one per line, silently
    skipping anything that isn't a well-formed JSON object -- vendor
    stream-json output interleaves structured events with (rare)
    non-JSON lines, and an adversarial/misbehaving driver may emit
    garbage; robustness here, not strictness, is the point (P5's fake
    driver's "adversarial" behaviour exercises exactly this)."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def parse_fake(stdout: str) -> DriverResult:
    """`muvue-fake-agent`'s own line protocol (see `src/muvue/fake_agent.py`):
    JSON lines, the last `{"type": "result", ...}` line wins."""
    objs = [o for o in _json_lines(stdout) if o.get("type") == "result"]
    if not objs:
        return DriverResult(status="failed", error="fake agent produced no result line")
    last = objs[-1]
    usage = last.get("usage") or {}
    return DriverResult(
        status=last.get("status", "failed"),
        summary=last.get("summary", ""),
        in_tokens=int(usage.get("in_tokens", 0)),
        out_tokens=int(usage.get("out_tokens", 0)),
        requests=int(last.get("requests", 1)),
        cost=float(usage.get("cost", 0.0)),
        model=last.get("model", "fake"),
        retry_after_seconds=last.get("retry_after_seconds"),
        error=last.get("error", ""),
    )


def parse_claude_stream_json(stdout: str) -> DriverResult:
    """SYNTHETIC/UNVERIFIED (see module docstring): reconstructed from
    Claude Code's documented `-p --output-format stream-json` shape --
    one JSON object per line, the terminal one `{"type": "result", ...}`
    carrying `usage: {input_tokens, output_tokens}`, `total_cost_usd`,
    `is_error`, and `result` (the final text). See
    tests/fixtures/vendor_samples/claude_stream_json_*.jsonl."""
    objs = [o for o in _json_lines(stdout) if o.get("type") == "result"]
    if not objs:
        return DriverResult(status="failed", error="no stream-json result event found")
    last = objs[-1]
    usage = last.get("usage") or {}
    is_error = bool(last.get("is_error"))
    result_text = last.get("result", "") or ""
    status = "done"
    if is_error:
        status = "rate_limited" if _is_rate_limit_text(result_text) else "failed"
    return DriverResult(
        status=status,
        summary=result_text if not is_error else "",
        in_tokens=int(usage.get("input_tokens", 0)),
        out_tokens=int(usage.get("output_tokens", 0)),
        cost=float(last.get("total_cost_usd", 0.0)),
        model=last.get("model"),
        error=result_text if is_error else "",
    )


def parse_codex_json(stdout: str) -> DriverResult:
    """SYNTHETIC/UNVERIFIED (see module docstring): reconstructed from
    `codex exec --json`'s documented event-stream shape -- one JSON object
    per line, a terminal `{"type": "task_complete", ...}` on success
    carrying `usage: {input_tokens, output_tokens}`, or
    `{"type": "error", "message": ...}` on failure. See
    tests/fixtures/vendor_samples/codex_json_*.jsonl."""
    objs = _json_lines(stdout)
    complete = [o for o in objs if o.get("type") == "task_complete"]
    errors = [o for o in objs if o.get("type") == "error"]
    if complete:
        last = complete[-1]
        usage = last.get("usage") or {}
        return DriverResult(
            status="done",
            summary=last.get("summary", ""),
            in_tokens=int(usage.get("input_tokens", 0)),
            out_tokens=int(usage.get("output_tokens", 0)),
            model=last.get("model"),
        )
    if errors:
        message = errors[-1].get("message", "")
        status = "rate_limited" if _is_rate_limit_text(message) else "failed"
        return DriverResult(status=status, error=message)
    return DriverResult(status="failed", error="no task_complete or error event found")


def parse_gemini_json(stdout: str) -> DriverResult:
    """SYNTHETIC/UNVERIFIED (see module docstring): a best-effort guess at
    a `gemini ... --json`-style single terminal JSON object carrying
    `usage: {promptTokenCount, candidatesTokenCount}` and `text` (Gemini
    API's own documented usage-metadata field names, used here as the
    closest available reference since no CLI-specific JSON output format
    was available to consult). See
    tests/fixtures/vendor_samples/gemini_json_*.jsonl."""
    objs = _json_lines(stdout)
    if not objs:
        return DriverResult(status="failed", error="no JSON object found in gemini output")
    last = objs[-1]
    if last.get("error"):
        message = str(last["error"])
        status = "rate_limited" if _is_rate_limit_text(message) else "failed"
        return DriverResult(status=status, error=message)
    usage = last.get("usage") or {}
    return DriverResult(
        status="done",
        summary=last.get("text", ""),
        in_tokens=int(usage.get("promptTokenCount", 0)),
        out_tokens=int(usage.get("candidatesTokenCount", 0)),
        model=last.get("model"),
    )


USAGE_PARSERS: dict[str, Callable[[str], DriverResult]] = {
    "fake": parse_fake,
    "claude_stream_json": parse_claude_stream_json,
    "codex_json": parse_codex_json,
    "gemini_json": parse_gemini_json,
}


def invoke_driver(
    agent_name: str,
    agent_cfg: AgentConfig,
    brief_text: str,
    cwd: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> DriverResult:
    """Run `auth_check` (if configured), then `command` with `brief_text`
    piped on stdin, in `cwd`. Never reads/stores/passes any vendor
    credential (plan section 1, principle 7) -- both commands run through
    the calling process's own inherited environment, exactly as running
    them by hand in a shell would; nothing here touches auth tokens.

    A failing `auth_check` -- driver-unavailable, e.g. not logged in --
    is reported as `status="unavailable"`, not raised: the runner must not
    crash on this, only handle it (block or fall back), per the P5
    prompt's requirement."""
    cwd = Path(cwd)
    if agent_cfg.auth_check.strip():
        check = subprocess.run(
            agent_cfg.auth_check, shell=True, cwd=cwd, capture_output=True, text=True,
        )
        if check.returncode != 0:
            return DriverResult(
                status="unavailable",
                error=(check.stderr or check.stdout or "auth_check failed").strip(),
            )

    try:
        proc = subprocess.run(
            agent_cfg.command,
            shell=True,
            cwd=cwd,
            input=brief_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return DriverResult(status="failed", error=f"driver timed out after {timeout}s")
    except OSError as e:
        return DriverResult(status="unavailable", error=str(e))

    parser = USAGE_PARSERS.get(agent_cfg.usage_parser)
    if parser is None:
        status = "done" if proc.returncode == 0 else "failed"
        result = DriverResult(status=status, error="" if status == "done" else proc.stderr)
    else:
        result = parser(proc.stdout)
        if proc.returncode != 0 and result.status == "done":
            # A nonzero exit always overrides a parser that otherwise
            # thought it saw success -- the process itself disagrees.
            result.status = "failed"
            result.error = result.error or proc.stderr.strip()

    result.raw_stdout = proc.stdout
    result.raw_stderr = proc.stderr
    return result
