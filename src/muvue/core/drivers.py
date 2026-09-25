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

`claude_stream_json` is tested against a recorded claude 2.1.281
session (tests/fixtures/vendor_samples/claude_stream_json_live_2_1_281.jsonl),
and `opencode_json` against recorded opencode 1.18.32 runs.
`codex_json` and `gemini_json` are reconstructed from each vendor's
documented output conventions and **not verified against a real
install**: Codex was logged out and Gemini not installed where they
were written. `docs/providers.md` says what was checked for each.
`fake` parses `muvue-fake-agent` (`src/muvue/fake_agent.py`), the
driver the test suite runs.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
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
    """`claude -p --output-format stream-json --verbose`, checked against
    claude 2.1.281 (tests/fixtures/vendor_samples/
    claude_stream_json_live_2_1_281.jsonl). One JSON object per line:
    `system/init` carries `model`, `rate_limit_event` carries
    `rate_limit_info` (`status`, `resetsAt` epoch seconds), and the
    terminal `result` carries `usage`, `modelUsage`, `total_cost_usd`,
    `is_error` and the final text in `result`.

    `in_tokens` counts the whole prompt, cached reads and cache writes
    included: `usage.input_tokens` alone is only the uncached remainder
    (18 of about 406k in the recorded run). An error result is a rate
    limit when the last `rate_limit_event` isn't `allowed`, or its text
    says so; `retry_after_seconds` comes from `resetsAt`."""
    objs = _json_lines(stdout)
    results = [o for o in objs if o.get("type") == "result"]
    if not results:
        return DriverResult(status="failed", error="no stream-json result event found")
    last = results[-1]
    usage = last.get("usage") or {}
    init = next((o for o in objs if o.get("type") == "system" and o.get("subtype") == "init"), {})
    rate_info = next(
        (o.get("rate_limit_info") or {} for o in reversed(objs) if o.get("type") == "rate_limit_event"), {},
    )
    model = last.get("model") or next(iter(last.get("modelUsage") or {}), None) or init.get("model")
    is_error = bool(last.get("is_error"))
    result_text = last.get("result", "") or ""
    status, retry_after = "done", None
    if is_error:
        limited = not str(rate_info.get("status", "allowed")).startswith("allowed")
        status = "rate_limited" if limited or _is_rate_limit_text(result_text) else "failed"
        resets_at = rate_info.get("resetsAt")
        if status == "rate_limited" and isinstance(resets_at, (int, float)):
            retry_after = max(0, int(resets_at - time.time()))
    in_tokens = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return DriverResult(
        status=status,
        summary=result_text if not is_error else "",
        in_tokens=in_tokens,
        out_tokens=int(usage.get("output_tokens", 0)),
        cost=float(last.get("total_cost_usd", 0.0)),
        model=model,
        retry_after_seconds=retry_after,
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


def parse_opencode_json(stdout: str) -> DriverResult:
    """`opencode run --format json`, checked against opencode 1.18.32
    (tests/fixtures/vendor_samples/opencode_json_live_1_18_32*.jsonl).
    One JSON object per line. Each model call ends with a `step_finish`
    whose `part` carries that call's `tokens` (`input`, `output`,
    `reasoning`, `cache.read`, `cache.write`) and `cost`, so usage is
    the sum over every step. The run succeeded when the last step
    finished with `reason: "stop"`; the final text is the last `text`
    part. A failure is a top-level `{"type": "error", "error": {"name",
    "data": {"message", "statusCode", "responseHeaders"}}}`, and a 429
    or rate-limit text makes it `rate_limited`, with `retry-after` from
    the response headers when present. The events don't name the model,
    so `model` stays None."""
    objs = _json_lines(stdout)
    steps = [o.get("part") or {} for o in objs if o.get("type") == "step_finish"]
    texts = [(o.get("part") or {}).get("text", "") for o in objs if o.get("type") == "text"]
    errors = [o.get("error") or {} for o in objs if o.get("type") == "error"]
    in_tokens = out_tokens = 0
    cost = 0.0
    for step in steps:
        tokens = step.get("tokens") or {}
        cache = tokens.get("cache") or {}
        in_tokens += int(tokens.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
        out_tokens += int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
        cost += float(step.get("cost") or 0.0)
    result = DriverResult(
        status="failed", in_tokens=in_tokens, out_tokens=out_tokens,
        requests=max(1, len(steps)), cost=cost,
    )
    if errors:
        data = errors[-1].get("data") or {}
        result.error = str(data.get("message") or errors[-1].get("name") or "opencode error")
        if data.get("statusCode") == 429 or _is_rate_limit_text(result.error):
            result.status = "rate_limited"
            retry_after = str((data.get("responseHeaders") or {}).get("retry-after", ""))
            if retry_after.isdigit():
                result.retry_after_seconds = int(retry_after)
        return result
    if not steps or steps[-1].get("reason") != "stop":
        result.error = "opencode run ended without a final step"
        return result
    result.status = "done"
    result.summary = texts[-1] if texts else ""
    return result


USAGE_PARSERS: dict[str, Callable[[str], DriverResult]] = {
    "fake": parse_fake,
    "claude_stream_json": parse_claude_stream_json,
    "codex_json": parse_codex_json,
    "gemini_json": parse_gemini_json,
    "opencode_json": parse_opencode_json,
}


# Driver subprocesses currently running in this process, so `stop_all`
# (called when the runner is told to stop) can kill them.
_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()


def stop_all(sig: int = signal.SIGTERM) -> int:
    """Signal every running driver's whole process group (the command runs
    through a shell, so the agent CLI is a grandchild). Returns how many
    were signalled."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    return len(procs)


def _run_streaming(
    command: str, cwd: Path, stdin_text: str, *, timeout: int, log_path: Path | None,
) -> tuple[int, str, str]:
    """Run `command` in its own session, feed `stdin_text`, and collect
    stdout/stderr -- copying each line to `log_path` as it arrives so a
    running node's output can be tailed (`GET /nodes/{id}/logs`). Raises
    `subprocess.TimeoutExpired` after killing the process group."""
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
    log = open(log_path, "a", encoding="utf-8") if log_path is not None else None
    log_lock = threading.Lock()
    chunks: dict[str, list[str]] = {"out": [], "err": []}

    def pump(stream, key: str, prefix: str) -> None:
        for line in iter(stream.readline, ""):
            chunks[key].append(line)
            if log is not None:
                with log_lock:
                    log.write(prefix + line)
                    log.flush()
        stream.close()

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, "out", ""), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "err", "[stderr] "), daemon=True),
    ]
    for r in readers:
        r.start()
    try:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
        for r in readers:
            r.join(timeout=5)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
        if log is not None:
            log.close()
    return proc.returncode, "".join(chunks["out"]), "".join(chunks["err"])


def invoke_driver(
    agent_name: str,
    agent_cfg: AgentConfig,
    brief_text: str,
    cwd: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    log_path: Path | None = None,
) -> DriverResult:
    """Run `auth_check` (if configured), then `command` with `brief_text`
    piped on stdin, in `cwd`. Never reads/stores/passes any vendor
    credential (plan section 1, principle 7) -- both commands run through
    the calling process's own inherited environment, exactly as running
    them by hand in a shell would; nothing here touches auth tokens.

    A failing `auth_check` -- driver-unavailable, e.g. not logged in --
    is reported as `status="unavailable"`, not raised: the runner must not
    crash on this, only handle it (block or fall back), per the P5
    prompt's requirement.

    `log_path`: append the driver's output there line by line as it runs.
    The command runs in its own session so `stop_all` can end it."""
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

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {agent_name}: "
                    f"{agent_cfg.command}\n")
    try:
        returncode, stdout, stderr = _run_streaming(
            agent_cfg.command, cwd, brief_text, timeout=timeout, log_path=log_path,
        )
    except subprocess.TimeoutExpired:
        return DriverResult(status="failed", error=f"driver timed out after {timeout}s")
    except OSError as e:
        return DriverResult(status="unavailable", error=str(e))

    parser = USAGE_PARSERS.get(agent_cfg.usage_parser)
    if parser is None:
        status = "done" if returncode == 0 else "failed"
        result = DriverResult(status=status, error="" if status == "done" else stderr)
    else:
        result = parser(stdout)
        if returncode != 0 and result.status == "done":
            # A nonzero exit always overrides a parser that otherwise
            # thought it saw success -- the process itself disagrees.
            result.status = "failed"
            result.error = result.error or stderr.strip()

    result.raw_stdout = stdout
    result.raw_stderr = stderr
    return result
