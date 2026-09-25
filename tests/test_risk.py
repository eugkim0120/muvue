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


# -- v4 section 5: touches outside predicted_touches raise the tier ---------
#
# "Inputs: diff size, path globs, deletions, criteria edits, and *touches
# outside predicted_touches*." P2b acceptance: "touch outside
# predicted_touches raises the tier." Never lowers it (§13: predicted_
# touches is a heuristic risk-scoring input, not a safety guarantee).


def _record_actual_touch(conn, node_id, path):
    conn.execute("INSERT INTO actual_touches (node_id, path) VALUES (?, ?)", (node_id, path))
    conn.commit()


def test_touches_outside_predicted_true_when_actual_touch_not_covered(conn, project):
    task = _task(conn, project, touches=["src/a.py"])
    _record_actual_touch(conn, task["id"], "src/unexpected.py")
    assert risk.touches_outside_predicted(conn, task["id"]) is True


def test_touches_outside_predicted_false_when_actual_touches_fully_covered(conn, project):
    task = _task(conn, project, touches=["src/*.py"])
    _record_actual_touch(conn, task["id"], "src/a.py")
    assert risk.touches_outside_predicted(conn, task["id"]) is False


def test_touches_outside_predicted_false_when_no_actual_touches_recorded(conn, project):
    task = _task(conn, project, touches=["src/a.py"])
    assert risk.touches_outside_predicted(conn, task["id"]) is False


def test_compute_tier_raises_low_to_medium_on_touches_outside_predicted(conn, project, config):
    task = _task(conn, project, touches=["a.py"])  # would otherwise be low
    assert (
        risk.compute_tier(conn, task, config, touches_outside_predicted=True) == "medium"
    )


def test_compute_tier_never_lowered_by_touches_outside_predicted(conn, project, config):
    """The flag only ever raises; a signal that would already push the
    node to 'high' (a glob match) is untouched by it."""
    config.risk.globs = ["migrations/**"]
    task = _task(conn, project, touches=["migrations/0001_init.sql"])
    assert (
        risk.compute_tier(conn, task, config, touches_outside_predicted=True) == "high"
    )


def test_compute_tier_isolates_touches_outside_predicted_from_other_signals(conn, project, config):
    """A node whose actual touches ARE fully within predicted globs is
    unaffected by this specific input -- isolating it from the touch-count
    tier signals covered by the other tests above."""
    task = _task(conn, project, touches=["a.py"])
    assert risk.compute_tier(conn, task, config, touches_outside_predicted=False) == "low"


@pytest.mark.parametrize("path", [
    "tests/test_api.py", "test_api.py", "pkg/tests/helpers.py", "src/foo_test.go",
    "web/app.test.ts", "web/app.spec.js", "conftest.py", "__tests__/x.js", "spec/models/user_spec.rb",
])
def test_is_test_touch_matches_test_shaped_paths(path):
    assert risk.is_test_touch(path)


@pytest.mark.parametrize("path", [
    "src/latest.py", "contest/rules.md", "docs/testimonials.md", "attestation.py", "src/fastest_path.py",
])
def test_is_test_touch_ignores_paths_that_merely_contain_test(path):
    """Regression: `"test" in path` flagged `latest.py`, `attestation.py`..."""
    assert not risk.is_test_touch(path)
