"""v4 section 4a: "`doctor` reports queue depth and warns above 1000."
Non-fatal -- `doctor.ok` stays True; `doctor.warnings` carries the
message. Also covers doctor's absolute-path shim check against the new
`PYTHONPATH=<dir> <python> -S -m muvue._hook NAME` shim format (v4
section 4a's `hook_fast_path_command`, not the pre-v4 `-m muvue hook`
form).

`skip_security_probes=True` throughout: v4 section 8a control 7 added a
live daemon-security probe pass to `run_doctor` (a real, if scratch,
`muvue serve` subprocess spin-up when nothing is already listening --
see tests/test_doctor_security_probes.py for its own dedicated
coverage), which is orthogonal to the queue-depth behavior this file
tests and would otherwise slow every test here down and add an
unrelated informational warning to the `report.warnings == []`
assertions below."""

from __future__ import annotations

from pathlib import Path

from muvue.core import doctor
from muvue.core.repo_init import init_repo


def _spool(repo_root: Path, n: int) -> None:
    path = repo_root / ".muvue" / "queue.jsonl"
    with path.open("a") as f:
        for i in range(n):
            f.write('{"event": "stop", "ts": "t%d", "node_id": null}\n' % i)


def test_doctor_ok_with_no_queue_file(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
    assert report.warnings == []


def test_doctor_ok_with_shallow_queue(tmp_path: Path):
    init_repo(tmp_path)
    _spool(tmp_path, 5)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
    assert report.warnings == []


def test_doctor_warns_above_1000_but_stays_ok(tmp_path: Path):
    init_repo(tmp_path)
    _spool(tmp_path, 1001)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
    assert len(report.warnings) == 1
    assert "1001" in report.warnings[0]
    assert "1000" in report.warnings[0]


def test_doctor_accepts_the_new_pythonpath_prefixed_shim_as_absolute(tmp_path: Path):
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
    assert report.issues == []


def test_doctor_flags_a_hand_written_relative_interpreter_shim(tmp_path: Path):
    init_repo(tmp_path)
    hook_path = tmp_path / ".git" / "hooks" / "post-commit"
    hook_path.write_text(
        "#!/bin/sh\n\n"
        "# >>> muvue hook post-commit >>>\n"
        "PYTHONPATH=./src python3 -S -m muvue._hook post-commit\n"
        "# <<< muvue hook post-commit <<<\n"
    )
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("does not use an absolute path" in issue for issue in report.issues)
