"""An install from an older muvue lacks hook shims and `.gitignore`
entries added since. `doctor` must report them, `doctor --repair` must
add them, and `uninit` must still restore the repository exactly --
including what the repair added."""

from __future__ import annotations

import json
from pathlib import Path

from muvue.core import doctor, repo_init
from test_init_uninit import _diff_snapshots, _snapshot
from conftest import make_git_fixture


def _as_older_install(repo: Path) -> None:
    """Undo what a newer init added: the prepare-commit-msg shim (and its
    manifest entry), and the later `.gitignore` entries."""
    shim = repo / ".git" / "hooks" / "prepare-commit-msg"
    manifest_path = repo / ".muvue" / repo_init.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    original = manifest.pop(str(shim))
    if original is None:
        shim.unlink()
    else:
        shim.write_text(original)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    gitignore = repo / ".gitignore"
    gitignore.write_text(
        "\n".join(line for line in gitignore.read_text().split("\n") if line != ".muvue/logs/")
    )


def test_doctor_reports_missing_gitignore_entries(tmp_path: Path):
    repo = make_git_fixture(tmp_path, "plain_python")
    repo_init.init_repo(repo)
    _as_older_install(repo)
    report = doctor.run_doctor(repo, skip_security_probes=True)
    assert report.ok is False
    assert any(".muvue/logs/" in issue for issue in report.issues)


def test_repair_brings_an_older_install_up_to_date(tmp_path: Path):
    repo = make_git_fixture(tmp_path, "plain_python")
    repo_init.init_repo(repo)
    _as_older_install(repo)
    report = doctor.run_doctor(repo, repair=True, skip_security_probes=True)
    assert report.ok is True, report.issues
    assert ".muvue/logs/" in (repo / ".gitignore").read_text().split("\n")
    assert (repo / ".git" / "hooks" / "prepare-commit-msg").exists()
    assert doctor.run_doctor(repo, skip_security_probes=True).ok is True


def test_uninit_after_repair_restores_the_repository(tmp_path: Path):
    repo = make_git_fixture(tmp_path, "plain_python")
    before = _snapshot(repo)
    repo_init.init_repo(repo)
    _as_older_install(repo)
    doctor.run_doctor(repo, repair=True, skip_security_probes=True)
    repo_init.uninit_repo(repo)
    assert _diff_snapshots(before, _snapshot(repo)) == ""


def test_uninit_keeps_edits_made_after_init(tmp_path: Path):
    repo = make_git_fixture(tmp_path, "plain_python")
    repo_init.init_repo(repo)
    gitignore = repo / ".gitignore"
    gitignore.write_text(gitignore.read_text() + "dist/\n")
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text(hook.read_text() + "echo my own post-commit step\n")
    repo_init.uninit_repo(repo)
    assert "dist/" in gitignore.read_text().split("\n")
    assert "muvue" not in gitignore.read_text()
    assert "echo my own post-commit step" in hook.read_text()
    assert "muvue" not in hook.read_text()


def _as_pre_v4_shim(repo: Path) -> Path:
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text(hook.read_text().replace(
        next(line for line in hook.read_text().splitlines() if "muvue._hook" in line),
        "/usr/bin/python3 -m muvue hook post-commit",
    ))
    return hook


def test_doctor_reports_a_pre_v4_shim_and_repair_upgrades_it(tmp_path: Path):
    repo = make_git_fixture(tmp_path, "plain_python")
    before = _snapshot(repo)
    repo_init.init_repo(repo)
    hook = _as_pre_v4_shim(repo)
    report = doctor.run_doctor(repo, skip_security_probes=True)
    assert report.ok is False
    assert any("outdated" in issue and "post-commit" in issue for issue in report.issues)

    report = doctor.run_doctor(repo, repair=True, skip_security_probes=True)
    assert report.ok is True, report.issues
    assert "-m muvue._hook post-commit" in hook.read_text()
    assert "-m muvue hook post-commit" not in hook.read_text()

    repo_init.uninit_repo(repo)
    assert _diff_snapshots(before, _snapshot(repo)) == ""
