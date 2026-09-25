"""Runner process registry (plan section 5 "Emergency stop: `pause` kills
runner processes"; section 8 "runner process management").

Each `muvue run` process registers itself as `.muvue/runners/<pid>.json`
(gitignored) for its lifetime: `{"pid", "project_id", "started_at"}`.
`stop` sends SIGTERM to the matching runners and waits for them to
unregister; the runner's handler kills its driver subprocesses and hands
their nodes back to `ready` without consuming an attempt
(`core.runner`). A runner that doesn't exit in time is killed, and its
leases expire as for any crash.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

RUNNERS_RELDIR = Path(".muvue") / "runners"


def _dir(repo_root: Path) -> Path:
    return Path(repo_root) / RUNNERS_RELDIR


def register(repo_root: Path, project_id: int | None) -> Path:
    path = _dir(repo_root) / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": os.getpid(),
        "project_id": project_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }))
    return path


def unregister(repo_root: Path) -> None:
    (_dir(repo_root) / f"{os.getpid()}.json").unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_muvue_process(pid: int) -> bool:
    """Guard against PID reuse: only signal a process that is still muvue."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"muvue" in f.read()
    except OSError:
        return _alive(pid)  # no /proc: best effort


def live(repo_root: Path) -> list[dict]:
    """Registered runners that are still alive; stale entries are removed."""
    out = []
    directory = _dir(repo_root)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        if _alive(entry["pid"]) and _is_muvue_process(entry["pid"]):
            out.append(entry)
        else:
            path.unlink(missing_ok=True)
    return out


def stop(repo_root: Path, project_id: int | None = None, *, timeout_s: float = 15.0) -> list[int]:
    """SIGTERM every live runner working on `project_id` (runners started
    without a project filter work on every project, so they are stopped
    too), wait up to `timeout_s` for them to exit, then SIGKILL any left.
    Returns the pids signalled."""
    targets = [
        r["pid"] for r in live(repo_root)
        if project_id is None or r["project_id"] in (None, project_id)
    ]
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    def running(pid: int) -> bool:
        # A runner removes its own entry on the way out; checking the file
        # too means an exited-but-unreaped (zombie) runner counts as gone.
        return (_dir(repo_root) / f"{pid}.json").exists() and _alive(pid)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and any(running(p) for p in targets):
        time.sleep(0.05)
    for pid in targets:
        if running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        (_dir(repo_root) / f"{pid}.json").unlink(missing_ok=True)
    return targets
