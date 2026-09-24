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
