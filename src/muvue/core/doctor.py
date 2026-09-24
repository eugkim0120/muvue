"""`doctor [--repair]`: sanity-check a .muvue/ install (plan section 5)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import adapters as adapters_mod
from . import db as core_db
from .config import ConfigError, load_config
from .repo_init import HOOK_NAMES, _hook_marker, _install_hook_shim


@dataclass
class DoctorReport:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)


def run_doctor(repo_root: Path, *, repair: bool = False) -> DoctorReport:
    repo_root = Path(repo_root)
    report = DoctorReport()
    muvue_dir = repo_root / ".muvue"

    if not muvue_dir.exists():
        report.fail(f"{muvue_dir} does not exist; run `muvue init`")
        return report

    config_path = muvue_dir / "config.toml"
    config = None
    if not config_path.exists():
        report.fail(f"{config_path} missing")
    else:
        try:
            config = load_config(config_path)
        except ConfigError as e:
            report.fail(str(e))

    db_path = muvue_dir / "muvue.db"
    if not db_path.exists():
        report.fail(f"{db_path} missing")
    else:
        conn = core_db.connect(db_path)
        try:
            version = core_db.get_schema_version(conn)
            if version == 0:
                report.fail(f"{db_path} has no schema_version recorded")
        finally:
            conn.close()

    husky_dir = repo_root / ".husky"
    for name in HOOK_NAMES:
        path = husky_dir / name if husky_dir.is_dir() else repo_root / ".git" / "hooks" / name
        begin, _ = _hook_marker(name)
        if not path.exists() or begin not in path.read_text():
            if repair:
                _install_hook_shim(path, name, {})
                report.repaired.append(f"reinstalled hook shim: {path}")
            else:
                report.fail(f"hook shim missing or not installed: {path} (--repair to fix)")
        else:
            content = path.read_text()
            # Verify the shim uses an absolute interpreter path (plan section 5).
            for line in content.splitlines():
                if line.strip().startswith("/") and " -m muvue hook " in line:
                    interp = line.split()[0].strip("'\"")
                    if not Path(interp).is_absolute():
                        report.fail(f"hook shim in {path} does not use an absolute path")
                    break

    # Adapter protocol_version drift (plan section 7): a `.claude/
    # settings.json` written by an older `muvue adapter install
    # claude-code` (a stale protocol_version embedded) needs re-running
    # after a protocol bump -- see docs/decisions.md.
    if config is not None:
        installed = adapters_mod.claude_code_protocol_version(repo_root)
        if installed is not None and installed != config.protocol_version:
            report.fail(
                f".claude/settings.json was installed for protocol_version={installed}, "
                f"repo is now protocol_version={config.protocol_version}; "
                "re-run `muvue adapter install claude-code`"
            )

    # Strict mode (plan section 5, P4): "Agent never holds a `main`
    # checkout" -- a node actively being worked (`in_progress`/`review`)
    # must have a bound worktree that still exists on disk, distinct from
    # `repo_root` itself. A missing binding or a worktree directory
    # that's disappeared (removed by hand, or `worktree_setup` never
    # completed for a pre-P4 in-progress node) is exactly the state
    # `doctor --repair` orphan-worktree handling (plan section 5,
    # "Leases") is meant to catch; P4 only adds detection, `--repair`
    # for worktrees is left for the daemon reconcile work that owns
    # leases (out of P4 scope).
    if config is not None and config.mode == "strict" and db_path.exists():
        conn = core_db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, status, worktree FROM nodes "
                "WHERE status IN ('in_progress', 'review') AND deleted_at IS NULL"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            worktree = row["worktree"]
            if worktree is None:
                report.fail(
                    f"node {row['id']} is {row['status']} in strict mode with no bound "
                    "worktree (agent would be holding the main checkout)"
                )
            elif not Path(worktree).exists():
                report.fail(
                    f"node {row['id']} is bound to worktree {worktree}, which no longer "
                    "exists on disk"
                )
            elif Path(worktree).resolve() == repo_root.resolve():
                report.fail(
                    f"node {row['id']}'s bound worktree is the main checkout {repo_root} "
                    "(strict mode must never point a node at main)"
                )

    return report
