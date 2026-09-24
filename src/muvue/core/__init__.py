"""muvue.core: the single write path (plan section 1, principle 1).

The CLI, daemon and git hooks all call into this package; nothing outside
it issues raw SQL against .muvue/muvue.db.
"""

from . import (
    adapters,
    asks,
    claude_hooks,
    daemon,
    db,
    doctor,
    drivers,
    events,
    gates,
    hooks,
    merge,
    migrate,
    nodes,
    projects,
    queries,
    rebuild,
    repo_init,
    review,
    revisions,
    risk,
    runner,
    state_machine,
    strict,
    trailers,
)
from .config import ConfigError, MuvueConfig, load_config

__all__ = [
    "adapters",
    "asks",
    "claude_hooks",
    "daemon",
    "db",
    "doctor",
    "drivers",
    "events",
    "gates",
    "hooks",
    "merge",
    "migrate",
    "nodes",
    "projects",
    "queries",
    "rebuild",
    "repo_init",
    "review",
    "revisions",
    "risk",
    "runner",
    "state_machine",
    "strict",
    "trailers",
    "ConfigError",
    "MuvueConfig",
    "load_config",
]
