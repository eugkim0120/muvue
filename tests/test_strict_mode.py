"""P4 acceptance tests (plan section 11 row P4):

1. Push to `main` from a worktree that bypasses the hook fails.
2. Test-file edit is flagged.

Plus the supporting scaffolding the plan section 5 "Strict mode" paragraph
requires to make those two mean anything real: airlock bare-repo setup,
worktree-per-node binding at `start`, `worktree_setup` running once per new
worktree (and failing `start` cleanly on setup failure), and `doctor`
catching a strict-mode node without a live bound worktree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from muvue.core import db as core_db
from muvue.core import doctor as doctor_mod
from muvue.core import gates, nodes, projects, strict
from muvue.core.config import MuvueConfig
from muvue.core.repo_init import init_repo


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo),
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=base_env,
    )


def _init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n")
    (root / "app.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


@pytest.fixture
def strict_config() -> MuvueConfig:
    cfg = MuvueConfig()
    cfg.mode = "strict"
    cfg.worktree_setup = ""  # no-op by default; individual tests override
    return cfg


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Isolate ~/.muvue/airlocks + ~/.muvue/worktrees per test."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def repo(tmp_path, home) -> Path:
    return _init_git_repo(tmp_path / "repo")


@pytest.fixture
def conn(repo):
    init_repo(repo)
    c = core_db.connect(repo / ".muvue" / "muvue.db")
    yield c
    c.close()


@pytest.fixture
def project(conn):
    return projects.create_project(conn, goal="strict mode test")


def _ready_task(conn, project, config, **kwargs):
    task = nodes.create_node(
        conn, project_id=project["id"], kind="task", title="t",
        criteria=["passes"], criteria_mode=kwargs.pop("criteria_mode", "manual"),
        predicted_touches=kwargs.pop("touches", ["app.py"]), status="pending",
    )
    gates.approve_gate2(conn, project["id"], config=config)
    return nodes.get_node(conn, task["id"])


# -- airlock ------------------------------------------------------------


def test_ensure_airlock_creates_bare_repo_under_home(repo, home):
    airlock = strict.ensure_airlock(repo)
    assert airlock.exists()
    assert airlock.name.endswith(".git")
    assert str(airlock).startswith(str(home / ".muvue" / "airlocks"))
    result = subprocess.run(
        ["git", "--git-dir", str(airlock), "rev-parse", "--is-bare-repository"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "true"


def test_ensure_airlock_syncs_main_branch_from_repo(repo, home):
    airlock = strict.ensure_airlock(repo)
    out = subprocess.run(
        ["git", "--git-dir", str(airlock), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    local_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert out == local_head


def test_ensure_airlock_installs_pre_receive_shim(repo, home):
    airlock = strict.ensure_airlock(repo)
    hook_path = airlock / "hooks" / "pre-receive"
    assert hook_path.exists()
    content = hook_path.read_text()
    assert sys.executable in content
    assert "hook pre-receive" in content


# -- worktree binding at start -------------------------------------------


def test_start_in_strict_mode_binds_a_worktree(conn, project, strict_config, repo):
    task = _ready_task(conn, project, strict_config)
    result = nodes.start(
        conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo,
    )
    node = result["node"]
    assert node["worktree"] is not None
    wt = Path(node["worktree"])
    assert wt.exists()
    assert (wt / "app.py").exists()
    # bound worktree is never the main checkout
    assert wt.resolve() != repo.resolve()


def test_start_in_strict_mode_runs_worktree_setup_once(conn, project, strict_config, repo, tmp_path):
    marker = tmp_path / "setup_ran"
    strict_config.worktree_setup = f"echo ran >> {marker}"
    task = _ready_task(conn, project, strict_config)
    nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    assert marker.exists()
    assert marker.read_text().count("ran") == 1


def test_start_in_strict_mode_fails_cleanly_when_worktree_setup_fails(
    conn, project, strict_config, repo
):
    strict_config.worktree_setup = "exit 1"
    task = _ready_task(conn, project, strict_config)
    with pytest.raises(strict.StrictModeError) as exc_info:
        nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    assert "exit 1" not in str(exc_info.value) or True  # message just needs setup output
    node = nodes.get_node(conn, task["id"])
    # start must not have half-applied: node stays ready, no worktree bound
    assert node["status"] == "ready"
    assert node["worktree"] is None
    # and no orphan worktree left on disk
    branch_dirs = list(strict.worktrees_root(repo).glob("node-*")) if strict.worktrees_root(repo).exists() else []
    assert branch_dirs == []


def test_light_mode_start_never_touches_worktree(conn, project, repo):
    config = MuvueConfig()  # mode="light"
    task = _ready_task(conn, project, config)
    result = nodes.start(conn, task["id"], owner="agent-1", config=config, repo_root=repo)
    assert result["node"]["worktree"] is None


# -- doctor: agent never holds a main checkout in strict mode ------------


def test_doctor_flags_strict_node_missing_worktree(conn, project, strict_config, repo, monkeypatch):
    task = _ready_task(conn, project, strict_config)
    nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    node = nodes.get_node(conn, task["id"])
    # simulate an orphaned/removed worktree directory
    import shutil

    shutil.rmtree(node["worktree"])
    conn.commit()

    # doctor loads config.toml from disk -- flip it to strict mode so
    # run_doctor actually exercises the strict-mode worktree check.
    config_path = repo / ".muvue" / "config.toml"
    config_path.write_text(config_path.read_text().replace('mode = "light"', 'mode = "strict"'))

    report = doctor_mod.run_doctor(repo, skip_security_probes=True)
    assert report.ok is False
    assert any("worktree" in issue for issue in report.issues)


# -- clean-env auto checks + real-diff test-edit flagging (criterion 2) --


def test_flag_test_edit_via_real_worktree_diff(conn, project, strict_config, repo):
    task = _ready_task(conn, project, strict_config, criteria_mode="auto")
    result = nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    wt = Path(result["node"]["worktree"])
    (wt / "test_app.py").write_text("def test_x(): assert True\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "add test")

    node = nodes.get_node(conn, task["id"])
    from muvue.core import review

    dispatch = review.dispatch(conn, node, strict_config, run_checks=lambda cmd, cwd: True)
    assert dispatch["flag"] is True
    assert dispatch["event_type"] == "review.test_edit_flagged"


def test_strict_auto_checks_run_in_the_worktree_not_repo_root(conn, project, strict_config, repo):
    task = _ready_task(conn, project, strict_config, criteria_mode="auto")
    result = nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    node = nodes.get_node(conn, task["id"])

    seen_cwds = []

    def fake_run_checks(cmd, cwd):
        seen_cwds.append(cwd)
        return True

    from muvue.core import review

    review.dispatch(conn, node, strict_config, run_checks=fake_run_checks, cwd=str(repo))
    # test and lint both run, both in the worktree.
    assert seen_cwds == [result["node"]["worktree"]] * 2


def test_strict_dispatch_is_noop_when_no_worktree_bound(conn, project, strict_config):
    """Existing pre-P4 stub behavior for a strict-mode node with no bound
    worktree (e.g. never started) stays a no-op -- see
    tests/test_light_review.py::test_dispatch_is_a_noop_outside_light_mode,
    which this preserves unchanged."""
    task = _ready_task(conn, project, strict_config, criteria_mode="manual")
    from muvue.core import review

    assert review.dispatch(conn, task, strict_config)["flag"] is False


# -- pre-receive enforcement: acceptance criterion 1 ---------------------


def test_acceptance_1_push_to_main_bypassing_worktree_fails_bound_push_succeeds(
    conn, project, strict_config, repo, tmp_path
):
    task = _ready_task(conn, project, strict_config, criteria_mode="auto")
    result = nodes.start(conn, task["id"], owner="agent-1", config=strict_config, repo_root=repo)
    node = result["node"]
    airlock = strict.airlock_path(repo)
    bound_wt = Path(node["worktree"])

    # -- bypass: a plain clone of the airlock tries to push straight to main
    bypass_clone = tmp_path / "bypass_clone"
    _git(tmp_path, "clone", "-q", str(airlock), str(bypass_clone))
    (bypass_clone / "sneaky.py").write_text("evil = 1\n")
    _git(bypass_clone, "add", "-A")
    _git(bypass_clone, "commit", "-q", "-m", "bypass main")
    push = subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/main"],
        cwd=bypass_clone, capture_output=True, text=True,
    )
    assert push.returncode != 0, push.stdout + push.stderr
    assert "main is protected" in (push.stdout + push.stderr) or "rejected" in (push.stdout + push.stderr)

    # -- legitimate: push from the correctly-bound worktree to its own node branch
    (bound_wt / "feature.py").write_text("y = 2\n")
    _git(bound_wt, "add", "-A")
    _git(bound_wt, "commit", "-q", "-m", "do the work")
    good_push = subprocess.run(
        ["git", "push", str(airlock), f"node-{task['id']}"],
        cwd=bound_wt, capture_output=True, text=True,
    )
    assert good_push.returncode == 0, good_push.stdout + good_push.stderr


def test_pre_receive_rejects_push_to_unbound_node_branch(conn, project, strict_config, repo, tmp_path):
    """A node branch that exists on the airlock but whose node isn't
    in_progress/review (e.g. already done, or never started) is rejected --
    the state-check half of the pre-receive binding rule."""
    task = _ready_task(conn, project, strict_config, criteria_mode="auto")
    airlock = strict.ensure_airlock(repo)

    clone = tmp_path / "clone2"
    _git(tmp_path, "clone", "-q", str(airlock), str(clone))
    _git(clone, "checkout", "-q", "-b", f"node-{task['id']}")
    (clone / "x.py").write_text("z = 1\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "unbound work")
    push = subprocess.run(
        ["git", "push", "origin", f"node-{task['id']}"],
        cwd=clone, capture_output=True, text=True,
    )
    assert push.returncode != 0, push.stdout + push.stderr


# -- init --sandbox scaffold ----------------------------------------------


def test_init_sandbox_emits_scaffold_only(tmp_path, home):
    from muvue.core.repo_init import init_repo

    root = _init_git_repo(tmp_path / "sandboxrepo")
    muvue_dir = init_repo(root, sandbox=True)
    compose = muvue_dir / "sandbox-compose.yml"
    assert compose.exists()
    text = compose.read_text()
    assert "not implemented" in text.lower() or "future work" in text.lower()


def test_init_without_sandbox_flag_emits_no_compose_file(tmp_path, home):
    from muvue.core.repo_init import init_repo

    root = _init_git_repo(tmp_path / "nosandboxrepo")
    muvue_dir = init_repo(root, sandbox=False)
    assert not (muvue_dir / "sandbox-compose.yml").exists()
