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
