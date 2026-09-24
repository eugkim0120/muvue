"""Pydantic v2 models for .muvue/config.toml (plan section 2)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class ConfigError(Exception):
    """Raised with a precise, human-readable message on invalid config.toml."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChecksConfig(StrictModel):
    test: str = "pytest -q"
    lint: str = "ruff check ."


class RiskConfig(StrictModel):
    globs: list[str] = Field(default_factory=list)
    max_diff_lines: int = 300


class PlanningConfig(StrictModel):
    max_files_per_task: int = 8
    max_subtasks: int = 6
    ask_timeout_minutes: int = 60


class NotifyConfig(StrictModel):
    url: str = ""


class BudgetConfig(StrictModel):
    unit: Literal["usd", "tokens", "requests"] = "usd"
    limit: float = 25


class AgentConfig(StrictModel):
    command: str
    auth_check: str = ""
    usage_parser: str = ""
    cost_model: Literal["usd", "tokens", "quota"]
    max_concurrency: int = 1
    on_rate_limit: str = "wait"
    pinned_version: str = ""

    @field_validator("on_rate_limit")
    @classmethod
    def _validate_on_rate_limit(cls, v: str) -> str:
        if v in ("wait", "pause") or v.startswith("fallback:"):
            return v
        raise ValueError(
            "on_rate_limit must be 'wait', 'pause', or 'fallback:<agent>' "
            f"(got {v!r})"
        )


class RoutingConfig(StrictModel):
    spec: str = "claude"
    task: str = "claude"
    subtask: str = "claude"


class MuvueConfig(StrictModel):
    schema_version: int = 1
    protocol_version: int = 1
    mode: Literal["light", "strict"] = "light"
    worktree_mode: Literal["branch", "per_node"] = "branch"
    worktree_setup: str = "uv sync"
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

    @field_validator("routing")
    @classmethod
    def _routing_agents_exist(cls, v: RoutingConfig, info) -> RoutingConfig:
        agents = info.data.get("agents") or {}
        if not agents:
            return v
        for kind, agent_name in (
            ("spec", v.spec),
            ("task", v.task),
            ("subtask", v.subtask),
        ):
            if agent_name not in agents:
                raise ValueError(
                    f"routing.{kind} references unknown agent {agent_name!r}; "
                    f"known agents: {sorted(agents)}"
                )
        return v


DEFAULT_CONFIG_TOML = """\
schema_version = 1
protocol_version = 1
mode = "light"
worktree_mode = "branch"
worktree_setup = "uv sync"

[checks]
test = "pytest -q"
lint = "ruff check ."

[risk]
globs = ["migrations/**", "auth/**", "*.sql", "**/test_*"]
max_diff_lines = 300

[planning]
max_files_per_task = 8
max_subtasks = 6
ask_timeout_minutes = 60

[notify]
url = ""

[budget]
unit = "usd"
limit = 25

[agents.fake]
command = "muvue-fake-agent"
cost_model = "tokens"

[routing]
spec = "fake"
task = "fake"
subtask = "fake"
"""


def load_config(path: str | Path) -> MuvueConfig:
    """Load and strictly validate config.toml, raising ConfigError with a
    precise message on any problem (missing file, bad TOML, bad schema)."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = tomllib.loads(p.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {p}: {e}") from e
    try:
        return MuvueConfig(**raw)
    except ValidationError as e:
        raise ConfigError(f"invalid config in {p}:\n{e}") from e
