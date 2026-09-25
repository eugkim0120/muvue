"""`muvue-fake-agent`: a real, invocable subprocess stand-in for a
subscription-authenticated vendor CLI (plan section 10's fake-agent
testing strategy, section 6's runner/drivers, P5).

`tests/fake_agent.py` (P3) is a lightweight in-process test fixture that
drives `muvue.core` directly -- useful for asserting core's own
enforcement, but not a real subprocess, and P3 explicitly scoped spawning
one as real out (plan section 12 working rule 7). P5's runner needs to
actually *spawn* a driver per node exactly the way it would spawn a real
`claude`/`codex`/`gemini` CLI, so `config.agents.fake.command` in the
plan's example config names this module's console-script entry point
(`muvue-fake-agent`, `pyproject.toml` `[project.scripts]`) -- see
docs/decisions.md for why a console-script was chosen over a `muvue
fake-agent` subcommand (this needs to behave like an independent vendor
binary, invoked exactly the way the runner invokes `claude`/`codex`, not
like a muvue-internal verb).

This process never touches `muvue.core` or `.muvue/muvue.db` -- like a
real vendor CLI, it knows nothing about muvue's process graph; it only
reads a brief off stdin and prints a scripted result. All of the actual
`core.nodes.start`/`done`/`fail`/`block` calls happen in the *runner*
process (`core/runner.py`), which parses this process's stdout via
`core.drivers.parse_fake`.

Behaviour is selected by `--behavior` or, if omitted, the
`MUVUE_FAKE_BEHAVIOR` env var (default `cooperative`) -- an env-var prefix
on the configured `command` (e.g. `command = "MUVUE_FAKE_BEHAVIOR=rate_limited
muvue-fake-agent"`) is how a test config makes one `[agents.*]` entry
simulate a specific vendor behaviour without a dedicated config field,
mirroring `core.strict.bind_worktree`'s `worktree_setup` use of
`shell=True` for the same reason: `core.drivers.invoke_driver` always
spawns `command` with `shell=True`, so an inline env-var assignment in the
command string works with no extra plumbing.

Scripted behaviours (plan section 10: "cooperative; lazy (early done,
vacuous lessons); adversarial (...); rate-limited"):

- `cooperative` -- normal success, realistic-looking usage.
- `lazy` -- succeeds immediately with a near-empty summary and minimal
  usage (mirrors `tests/fake_agent.py::lazy_early_done`'s spirit, at the
  driver-subprocess level).
- `adversarial` -- emits noisy, partly-malformed extra output *and* a
  trailing garbage line after its real result line, to exercise
  `core.drivers`' parsers' robustness (only the last well-formed
  `{"type": "result", ...}` line is meant to win).
- `rate_limited` -- reports `status: "rate_limited"` with a
  `retry_after_seconds`, simulating a vendor CLI's rate-limit signal (P5
  acceptance criterion 4).
- `crash` -- exits non-zero having printed no parseable result line at
  all, simulating a driver crash (not a rate limit, not a clean failure
  report).
- `failed` -- reports a clean `status: "failed"` result (the driver ran
  and cleanly reported it could not complete the work).
- `slow` -- prints progress, sleeps `MUVUE_FAKE_SLEEP_SECONDS` (default
  30), then succeeds: a long-running agent for pause and log-tail tests.

`--check` runs this script in "auth_check" mode instead (e.g.
`auth_check = "muvue-fake-agent --check"`): prints a version line and
exits 0, or exits 1 if `MUVUE_FAKE_AUTH_FAIL` is set, simulating a
logged-out vendor CLI (P5's `auth_check` / driver-unavailable path).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BEHAVIORS = ("cooperative", "lazy", "adversarial", "rate_limited", "crash", "failed", "slow")


def _result_line(**fields) -> str:
    return json.dumps({"type": "result", **fields})


def _run(behavior: str, brief_raw: str) -> int:
    # A real vendor CLI would stream progress events before its final
    # result line; a couple of harmless non-result JSON lines here keep
    # the shape realistic and exercise the parser's "only the last result
    # line wins" contract.
    print(json.dumps({"type": "progress", "message": "reading brief"}), flush=True)
    print(json.dumps({"type": "progress", "message": f"behavior={behavior}"}), flush=True)

    if behavior == "slow":
        import time

        print(json.dumps({"type": "progress", "message": "working slowly"}), flush=True)
        time.sleep(float(os.environ.get("MUVUE_FAKE_SLEEP_SECONDS", "30")))
        behavior = "cooperative"

    if behavior == "cooperative":
        print(_result_line(
            status="done", summary="did the work",
            usage={"in_tokens": 1200, "out_tokens": 400}, model="fake",
        ))
        return 0

    if behavior == "lazy":
        print(_result_line(
            status="done", summary="",
            usage={"in_tokens": 50, "out_tokens": 5}, model="fake",
        ))
        return 0

    if behavior == "adversarial":
        # Noisy/malformed lines before and after the real result line --
        # core.drivers._json_lines must silently skip these, and only the
        # trailing well-formed result line (not the garbage after it)
        # should be used.
        print("not json at all")
        print(_result_line(
            status="done", summary="adversarial but reports done",
            usage={"in_tokens": 10, "out_tokens": 10}, model="fake",
        ))
        print("{\"type\": \"result\", \"status\": broken-json")
        return 0

    if behavior == "rate_limited":
        retry_after = int(os.environ.get("MUVUE_FAKE_RETRY_AFTER_SECONDS", "30"))
        print(_result_line(
            status="rate_limited", summary="",
            usage={"in_tokens": 300, "out_tokens": 0}, model="fake",
            retry_after_seconds=retry_after, error="rate limit exceeded",
        ))
        return 0

    if behavior == "failed":
        print(_result_line(
            status="failed", summary="",
            usage={"in_tokens": 80, "out_tokens": 0}, model="fake",
            error="fake agent scripted failure",
        ))
        return 0

    if behavior == "crash":
        sys.stderr.write("muvue-fake-agent: simulated crash, no result produced\n")
        return 1

    sys.stderr.write(f"muvue-fake-agent: unknown behavior {behavior!r}\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="muvue-fake-agent")
    parser.add_argument("--behavior", choices=BEHAVIORS, default=None)
    parser.add_argument(
        "--check", action="store_true",
        help="auth_check mode: print a version line and exit 0 (or 1 if "
        "MUVUE_FAKE_AUTH_FAIL is set), simulating a vendor CLI login check",
    )
    args = parser.parse_args(argv)

    if args.check:
        if os.environ.get("MUVUE_FAKE_AUTH_FAIL"):
            sys.stderr.write("muvue-fake-agent: not logged in (simulated)\n")
            return 1
        print("muvue-fake-agent 0.1.0 (synthetic vendor-CLI stand-in, not a real agent)")
        return 0

    behavior = args.behavior or os.environ.get("MUVUE_FAKE_BEHAVIOR", "cooperative")
    brief_raw = sys.stdin.read()
    return _run(behavior, brief_raw)


if __name__ == "__main__":
    raise SystemExit(main())
