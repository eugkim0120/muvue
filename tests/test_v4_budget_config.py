"""v4 section 2 Delta A: per-driver `[agents.<x>.budget]` replaces the
single global `[budget]` unit/limit. The top-level `[budget]` is now
unit-free stop conditions only (`max_wall_clock_minutes`,
`max_nodes_per_run`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from muvue.core.config import (
    AgentBudgetConfig,
    AgentConfig,
    BudgetConfig,
    ConfigError,
    DEFAULT_CONFIG_TOML,
    load_config,
)


def test_default_config_has_unit_free_top_level_budget(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    assert cfg.budget.max_wall_clock_minutes == 240
    assert cfg.budget.max_nodes_per_run == 20
    assert not hasattr(cfg.budget, "unit")
    assert not hasattr(cfg.budget, "limit")


def test_top_level_budget_rejects_old_unit_limit_shape(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML.replace(
        "[budget]\nmax_wall_clock_minutes = 240\nmax_nodes_per_run = 20",
        '[budget]\nunit = "usd"\nlimit = 25',
    ))
    with pytest.raises(ConfigError, match="unit"):
        load_config(p)


def test_agent_budget_nested_under_agent(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        DEFAULT_CONFIG_TOML
        + '\n[agents.fake.budget]\nunit = "tokens"\nlimit = 500\n'
    )
    cfg = load_config(p)
    assert cfg.agents["fake"].budget.unit == "tokens"
    assert cfg.agents["fake"].budget.limit == 500


def test_agent_without_budget_section_has_none():
    cfg = AgentConfig(command="x", cost_model="usd")
    assert cfg.budget is None


def test_agent_budget_config_requires_unit_and_limit():
    with pytest.raises(ValidationError):
        AgentBudgetConfig(unit="usd")


# -- v4 section 6 / changelog item 11: max_wait_minutes / on_rate_limit_timeout


def test_agent_config_defaults_max_wait_and_timeout_action():
    cfg = AgentConfig(command="x", cost_model="usd")
    assert cfg.max_wait_minutes == 30
    assert cfg.on_rate_limit_timeout == "pause"


def test_on_rate_limit_timeout_rejects_wait():
    with pytest.raises(ValidationError, match="wait"):
        AgentConfig(command="x", cost_model="usd", on_rate_limit_timeout="wait")


def test_on_rate_limit_timeout_accepts_pause_and_fallback():
    AgentConfig(command="x", cost_model="usd", on_rate_limit_timeout="pause")
    AgentConfig(command="x", cost_model="usd", on_rate_limit_timeout="fallback:other")


def test_on_rate_limit_timeout_rejects_garbage():
    with pytest.raises(ValidationError):
        AgentConfig(command="x", cost_model="usd", on_rate_limit_timeout="nonsense")
