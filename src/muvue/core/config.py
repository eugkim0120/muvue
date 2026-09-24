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
    lease_minutes: int = 60


class NotifyConfig(StrictModel):
    url: str = ""


class BudgetConfig(StrictModel):
    """v4 section 2 "Budget rule (changed from v3)": the top-level
    `[budget]` no longer holds a unit/limit -- that moved per-driver
    (`AgentBudgetConfig` below). This is now unit-free stop conditions
    only, checked by `core.runner.run` independently of any driver's own
    budget state."""

    max_wall_clock_minutes: int = 240
    max_nodes_per_run: int = 20


class AgentBudgetConfig(StrictModel):
    """`[agents.<x>.budget]` (v4 section 2): a driver's own budget, in a
    unit its `cost_model` can actually emit. `doctor` errors if `unit`
    isn't producible by the owning `AgentConfig.cost_model` -- see
    `core.runner.EXPECTED_BUDGET_UNIT` (decision #72's existing
    cost_model->unit mapping, mirrored, not reinvented) and
    `core.doctor.run_doctor`."""

    unit: Literal["usd", "tokens", "requests"]
    limit: float


class AgentConfig(StrictModel):
    command: str
    auth_check: str = ""
    usage_parser: str = ""
    cost_model: Literal["usd", "tokens", "quota"]
    max_concurrency: int = 1
    on_rate_limit: str = "wait"
    pinned_version: str = ""
    budget: AgentBudgetConfig | None = None
    # v4 section 6 / changelog item 11: "wait" against `on_rate_limit` is
    # bounded by `max_wait_minutes`, after which `on_rate_limit_timeout`
    # applies.
    max_wait_minutes: int = 30
    on_rate_limit_timeout: str = "pause"

    @field_validator("on_rate_limit")
    @classmethod
    def _validate_on_rate_limit(cls, v: str) -> str:
        if v in ("wait", "pause") or v.startswith("fallback:"):
            return v
        raise ValueError(
            "on_rate_limit must be 'wait', 'pause', or 'fallback:<agent>' "
            f"(got {v!r})"
        )

    @field_validator("on_rate_limit_timeout")
    @classmethod
    def _validate_on_rate_limit_timeout(cls, v: str) -> str:
        # v4 section 6 doesn't spell this out explicitly, but "wait" as a
        # *timeout* action would be an infinite loop (the thing
        # max_wait_minutes exists to prevent) -- see docs/decisions.md.
        if v == "pause" or v.startswith("fallback:"):
            return v
        raise ValueError(
            "on_rate_limit_timeout must be 'pause' or 'fallback:<agent>' "
            f"('wait' is not allowed -- it would be a silent infinite wait, "
            f"exactly what max_wait_minutes exists to bound) (got {v!r})"
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
lease_minutes = 60

[notify]
url = ""

[budget]
max_wall_clock_minutes = 240
max_nodes_per_run = 20

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
