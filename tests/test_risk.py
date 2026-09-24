"""P2: risk-tier computation is one source of truth (plan section 5).

Covers diff size (predicted_touches count), path globs, deletions,
criteria-edit-forces-high, and the never-downgrade rule that P2 acceptance
#4 depends on."""

from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import nodes, projects, risk
from muvue.core.config import MuvueConfig


@pytest.fixture
def conn(tmp_path: Path):
    c = core_db.init_db(tmp_path / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def config() -> MuvueConfig:
    return MuvueConfig()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="risk test")


def _task(conn, project, touches=None):
    return nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        predicted_touches=touches or [], status="pending",
    )


def test_low_tier_by_default(conn, project, config):
    task = _task(conn, project, touches=["a.py"])
    assert risk.compute_tier(conn, task, config) == "low"


def test_medium_tier_when_touches_exceed_max_files(conn, project, config):
    touches = [f"f{i}.py" for i in range(config.planning.max_files_per_task + 1)]
    task = _task(conn, project, touches=touches)
    assert risk.compute_tier(conn, task, config) == "medium"


def test_high_tier_when_touches_exceed_max_diff_lines(conn, project, config):
    touches = [f"f{i}.py" for i in range(config.risk.max_diff_lines + 1)]
    task = _task(conn, project, touches=touches)
    assert risk.compute_tier(conn, task, config) == "high"


def test_high_tier_on_glob_match(conn, project, config):
    config.risk.globs = ["migrations/**"]
    task = _task(conn, project, touches=["migrations/0001_init.sql"])
    assert risk.compute_tier(conn, task, config) == "high"


def test_high_tier_on_deletions(conn, project, config):
    task = _task(conn, project, touches=["a.py"])
    assert risk.compute_tier(conn, task, config, has_deletions=True) == "high"


def test_high_tier_on_criteria_edited_overrides_everything(conn, project, config):
    task = _task(conn, project, touches=["a.py"])
    assert risk.compute_tier(conn, task, config, criteria_edited=True) == "high"


def test_max_tier_never_downgrades():
    assert risk.max_tier("low", "high") == "high"
    assert risk.max_tier("high", "low") == "high"
    assert risk.max_tier("medium", "low") == "medium"
    assert risk.max_tier("low", "low") == "low"


def test_is_flagged_on_test_touch(conn, project, config):
    task = _task(conn, project, touches=["tests/test_foo.py"])
    assert risk.is_flagged(conn, task, config) is True


def test_is_flagged_false_when_no_test_touch(conn, project, config):
    task = _task(conn, project, touches=["a.py"])
    assert risk.is_flagged(conn, task, config) is False
