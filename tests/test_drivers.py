"""P5 acceptance criterion 1 (driver config parsing / subprocess invocation
shape) and plan section 10's "driver parser tests against recorded CLI
output samples for each vendor": since no network access / logged-in
vendor CLI exists in this environment, these samples are synthetic and
clearly labeled as such (see tests/fixtures/vendor_samples/README.md).
`test_invoke_driver_*` exercise `core.drivers.invoke_driver` against a
real subprocess (`muvue-fake-agent`), not a mock, since that's the actual
invocation mechanism being verified."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from muvue.core import drivers
from muvue.core.config import AgentConfig

FIXTURES = Path(__file__).parent / "fixtures" / "vendor_samples"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# -- synthetic vendor parser shapes ------------------------------------------


def test_parse_claude_stream_json_success():
    result = drivers.parse_claude_stream_json(_read("claude_stream_json_success.jsonl"))
    assert result.status == "done"
    assert result.in_tokens == 4210
    assert result.out_tokens == 812
    assert result.cost == pytest.approx(0.083)
    assert result.model == "claude-synthetic-1"


def test_parse_claude_stream_json_rate_limited():
    result = drivers.parse_claude_stream_json(_read("claude_stream_json_rate_limited.jsonl"))
    assert result.status == "rate_limited"


# -- recorded from claude 2.1.281 (the live P5 run, sanitized) ---------------

LIVE_CLAUDE = "claude_stream_json_live_2_1_281.jsonl"


def test_parse_claude_live_success():
    result = drivers.parse_claude_stream_json(_read(LIVE_CLAUDE))
    assert result.status == "done"
    # The model is on `system/init` and in `modelUsage`, not on `result`.
    assert result.model == "claude-sonnet-5"
    # Prompt tokens include the cached ones: 18 uncached is 0.004% of the
    # prompt the session actually processed.
    assert result.in_tokens == 18 + 34654 + 371321
    assert result.out_tokens == 1402
    assert result.cost == pytest.approx(0.2269362)
    assert result.summary.startswith("Added `add(a, b)`")


def _live_with(result_patch: dict, rate_status: str) -> str:
    """The live sample with its final events changed. Derived, not
    recorded: no real rate limit was hit during the run."""
    import json

    lines = []
    for line in _read(LIVE_CLAUDE).splitlines():
        obj = json.loads(line)
        if obj["type"] == "rate_limit_event":
            obj["rate_limit_info"]["status"] = rate_status
        if obj["type"] == "result":
            obj.update(result_patch)
        lines.append(json.dumps(obj))
    return "\n".join(lines) + "\n"


def test_parse_claude_live_shape_rate_limited_uses_reset_time(monkeypatch):
    import json

    info = next(
        json.loads(l)["rate_limit_info"] for l in _read(LIVE_CLAUDE).splitlines()
        if json.loads(l)["type"] == "rate_limit_event"
    )
    monkeypatch.setattr(drivers.time, "time", lambda: info["resetsAt"] - 600)
    stdout = _live_with({"is_error": True, "subtype": "error", "result": "usage limit reached"}, "rejected")
    result = drivers.parse_claude_stream_json(stdout)
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 600


def test_parse_claude_live_shape_error_while_allowed_is_a_failure():
    stdout = _live_with({"is_error": True, "subtype": "error_during_execution", "result": "tool crashed"}, "allowed")
    result = drivers.parse_claude_stream_json(stdout)
    assert result.status == "failed"
    assert result.error == "tool crashed"


def test_parse_codex_json_success():
    result = drivers.parse_codex_json(_read("codex_json_success.jsonl"))
    assert result.status == "done"
    assert result.in_tokens == 3100
    assert result.out_tokens == 640


def test_parse_codex_json_rate_limited():
    result = drivers.parse_codex_json(_read("codex_json_error.jsonl"))
    assert result.status == "rate_limited"


def test_parse_gemini_json_success():
    result = drivers.parse_gemini_json(_read("gemini_json_success.jsonl"))
    assert result.status == "done"
    assert result.in_tokens == 2800
    assert result.out_tokens == 510


def test_parse_gemini_json_rate_limited():
    result = drivers.parse_gemini_json(_read("gemini_json_error.jsonl"))
    assert result.status == "rate_limited"


# -- recorded from opencode 1.18.32 (deepseek-v4.1-flash, sanitized) ------

LIVE_OPENCODE = "opencode_json_live_1_18_32.jsonl"


def test_parse_opencode_live_success_sums_every_step():
    result = drivers.parse_opencode_json(_read(LIVE_OPENCODE))
    assert result.status == "done"
    # Four model calls; each step_finish reports its own tokens and cost.
    assert result.requests == 4
    assert result.in_tokens == (9592 + 1760) + (367 + 11222) + (383 + 11460) + (217 + 11714)
    assert result.out_tokens == (47 + 168) + (108 + 131) + 50 + 3
    assert result.cost == pytest.approx(0.003993036)
    assert result.summary == "Done"


def test_parse_opencode_live_auth_error_is_a_failure():
    result = drivers.parse_opencode_json(_read("opencode_json_live_1_18_32_auth_error.jsonl"))
    assert result.status == "failed"
    assert "Invalid credential" in result.error


def test_parse_opencode_429_is_rate_limited_with_retry_after():
    # Hand-constructed: the recorded APIError shape with a 429 status.
    stdout = (
        '{"type":"error","error":{"name":"APIError","data":{"message":"Too Many Requests",'
        '"statusCode":429,"isRetryable":true,"responseHeaders":{"retry-after":"120"}}}}\n'
    )
    result = drivers.parse_opencode_json(stdout)
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 120


def test_parse_opencode_error_after_steps_is_not_done():
    lines = _read(LIVE_OPENCODE).splitlines()[:3]
    lines.append('{"type":"error","error":{"name":"UnknownError","data":{"message":"boom"}}}')
    result = drivers.parse_opencode_json("\n".join(lines))
    assert result.status == "failed"
    assert result.error == "boom"
    assert result.in_tokens == 9592 + 1760


def test_parse_opencode_no_final_step_is_a_failure():
    result = drivers.parse_opencode_json(_read(LIVE_OPENCODE).splitlines()[0])
    assert result.status == "failed"


def test_parse_fake_takes_last_result_line():
    stdout = (
        '{"type": "progress", "message": "x"}\n'
        '{"type": "result", "status": "done", "summary": "first", "usage": {"in_tokens": 1, "out_tokens": 1}}\n'
        '{"type": "result", "status": "done", "summary": "second", "usage": {"in_tokens": 2, "out_tokens": 2}}\n'
    )
    result = drivers.parse_fake(stdout)
    assert result.summary == "second"
    assert result.in_tokens == 2


def test_parse_fake_no_result_line_is_failed():
    result = drivers.parse_fake("garbage\nmore garbage\n")
    assert result.status == "failed"


# -- config parsing / dispatch shape (claude/codex/gemini agent entries) ----


def test_agent_config_accepts_documented_driver_fields():
    cfg = AgentConfig(
        command="claude -p --output-format stream-json",
        auth_check="claude --version",
        usage_parser="claude_stream_json",
        cost_model="usd",
        max_concurrency=2,
        on_rate_limit="fallback:codex",
    )
    assert cfg.usage_parser == "claude_stream_json"
    assert cfg.cost_model == "usd"
    assert cfg.on_rate_limit == "fallback:codex"


def test_agent_config_codex_and_gemini_shapes():
    codex = AgentConfig(
        command="codex exec --json", usage_parser="codex_json", cost_model="usd",
    )
    gemini = AgentConfig(
        command="gemini --json", usage_parser="gemini_json", cost_model="tokens",
    )
    assert drivers.USAGE_PARSERS[codex.usage_parser] is drivers.parse_codex_json
    assert drivers.USAGE_PARSERS[gemini.usage_parser] is drivers.parse_gemini_json


def test_on_rate_limit_rejects_unknown_value():
    with pytest.raises(Exception):
        AgentConfig(command="x", usage_parser="fake", cost_model="tokens", on_rate_limit="nonsense")


# -- real subprocess invocation (the `fake` driver stands in for a real
#    vendor CLI -- see tests/test_runner.py's module docstring) ------------


FAKE_AGENT_BIN = shutil.which("muvue-fake-agent")


@pytest.fixture
def fake_cfg() -> AgentConfig:
    return AgentConfig(command="muvue-fake-agent", usage_parser="fake", cost_model="tokens")


@pytest.mark.skipif(FAKE_AGENT_BIN is None, reason="muvue-fake-agent console script not installed")
def test_invoke_driver_real_subprocess_cooperative(tmp_path, fake_cfg):
    result = drivers.invoke_driver("fake", fake_cfg, "{}", tmp_path)
    assert result.status == "done"
    assert result.in_tokens == 1200
    assert result.out_tokens == 400


@pytest.mark.skipif(FAKE_AGENT_BIN is None, reason="muvue-fake-agent console script not installed")
def test_invoke_driver_real_subprocess_rate_limited(tmp_path):
    cfg = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=rate_limited muvue-fake-agent",
        usage_parser="fake", cost_model="tokens",
    )
    result = drivers.invoke_driver("fake", cfg, "{}", tmp_path)
    assert result.status == "rate_limited"
    assert result.retry_after_seconds == 30


@pytest.mark.skipif(FAKE_AGENT_BIN is None, reason="muvue-fake-agent console script not installed")
def test_invoke_driver_real_subprocess_adversarial_output_is_still_parsed(tmp_path):
    cfg = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=adversarial muvue-fake-agent",
        usage_parser="fake", cost_model="tokens",
    )
    result = drivers.invoke_driver("fake", cfg, "{}", tmp_path)
    assert result.status == "done"
    assert result.summary == "adversarial but reports done"


@pytest.mark.skipif(FAKE_AGENT_BIN is None, reason="muvue-fake-agent console script not installed")
def test_invoke_driver_real_subprocess_crash(tmp_path):
    cfg = AgentConfig(
        command="MUVUE_FAKE_BEHAVIOR=crash muvue-fake-agent",
        usage_parser="fake", cost_model="tokens",
    )
    result = drivers.invoke_driver("fake", cfg, "{}", tmp_path)
    assert result.status == "failed"


@pytest.mark.skipif(FAKE_AGENT_BIN is None, reason="muvue-fake-agent console script not installed")
def test_invoke_driver_auth_check_failure_is_unavailable_not_a_crash(tmp_path):
    cfg = AgentConfig(
        command="muvue-fake-agent",
        auth_check="MUVUE_FAKE_AUTH_FAIL=1 muvue-fake-agent --check",
        usage_parser="fake", cost_model="tokens",
    )
    result = drivers.invoke_driver("fake", cfg, "{}", tmp_path)
    assert result.status == "unavailable"
    assert result.error


def test_invoke_driver_never_touches_credential_env_vars(tmp_path, fake_cfg, monkeypatch):
    """Plan section 1 principle 7: muvue never holds agent credentials.
    invoke_driver must not read, log, or forward any credential-shaped
    env var -- it only shells out and inherits the caller's environment
    unmodified, exactly like running the command by hand."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-read-by-muvue")
    import inspect

    source = inspect.getsource(drivers)
    assert "ANTHROPIC_API_KEY" not in source
    assert "os.environ" not in source
    result = drivers.invoke_driver("fake", fake_cfg, "{}", tmp_path)
    assert result.status in ("done", "unavailable")  # ran without touching the var
