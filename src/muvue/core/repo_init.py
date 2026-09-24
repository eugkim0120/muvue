"""`init` / `uninit` ops verbs: scaffold .muvue/, install git-hook shims,
and reverse the process byte-for-byte (plan section 5 "Shims", P0 acceptance
#3: init then uninit leaves `git status` clean).

Every file init touches (creates or appends to) is recorded, with its
original content (or None if it didn't exist), in
.muvue/.init_manifest.json. uninit replays that manifest to restore exactly
what was there before, then removes .muvue/ entirely.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from . import db as core_db
from .config import DEFAULT_CONFIG_TOML

HOOK_NAMES = ["post-commit", "pre-push"]
MANIFEST_NAME = ".init_manifest.json"


class AlreadyInitialized(Exception):
    pass


class NotInitialized(Exception):
    pass


def _hook_marker(name: str) -> tuple[str, str]:
    return (f"# >>> muvue hook {name} >>>", f"# <<< muvue hook {name} <<<")


def _gitignore_marker() -> tuple[str, str]:
    return ("# >>> muvue >>>", "# <<< muvue <<<")


def _install_hook_shim(path: Path, name: str, backups: dict[str, str | None]) -> None:
    key = str(path)
    if key not in backups:
        backups[key] = path.read_text() if path.exists() else None

    begin, end = _hook_marker(name)
    py = shlex.quote(sys.executable)
    body = f"{py} -m muvue hook {name}\n"
    block = f"{begin}\n{body}{end}\n"

    if path.exists():
        content = path.read_text()
        if begin in content:
            return  # already installed, nothing to do
        sep = "" if content.endswith("\n") or content == "" else "\n"
        new_content = content + sep + "\n" + block
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_content = "#!/bin/sh\n\n" + block

    path.write_text(new_content)
    path.chmod(path.stat().st_mode | 0o111)


def _update_gitignore(repo_root: Path, backups: dict[str, str | None]) -> None:
    path = repo_root / ".gitignore"
    key = str(path)
    if key not in backups:
        backups[key] = path.read_text() if path.exists() else None

    begin, end = _gitignore_marker()
    content = path.read_text() if path.exists() else ""
    if begin in content:
        return
    entries = [
        ".muvue/muvue.db",
        ".muvue/muvue.db-wal",
        ".muvue/muvue.db-shm",
        ".muvue/session",
        ".muvue/current_node",
    ]
    # A repo may already ignore one of these as a plain (unmarked) line --
    # don't duplicate it inside the marker block (dogfood-gate follow-up,
    # docs/decisions.md #40). Exact-line match only: substring matching
    # would also skip ".muvue/muvue.db" against ".muvue/muvue.db-wal".
    existing_lines = set(content.splitlines())
    entries = [e for e in entries if e not in existing_lines]
    lines = [begin, *entries, end, ""]
    sep = "" if content == "" or content.endswith("\n") else "\n"
    path.write_text(content + sep + "\n".join(lines))


def init_repo(repo_root: Path) -> Path:
    """Scaffold .muvue/ in repo_root. Returns the .muvue directory path."""
    repo_root = Path(repo_root)
    muvue_dir = repo_root / ".muvue"
    if muvue_dir.exists():
        raise AlreadyInitialized(f"{muvue_dir} already exists")

    muvue_dir.mkdir()
    (muvue_dir / "history").mkdir()
    (muvue_dir / "config.toml").write_text(DEFAULT_CONFIG_TOML)
    (muvue_dir / "components.json").write_text("[]\n")
    (muvue_dir / "decisions.json").write_text("[]\n")
    core_db.init_db(muvue_dir / "muvue.db").close()

    backups: dict[str, str | None] = {}
    husky_dir = repo_root / ".husky"
    for name in HOOK_NAMES:
        if husky_dir.is_dir():
            path = husky_dir / name
        else:
            path = repo_root / ".git" / "hooks" / name
        _install_hook_shim(path, name, backups)
    _update_gitignore(repo_root, backups)

    (muvue_dir / MANIFEST_NAME).write_text(json.dumps(backups, indent=2))
    return muvue_dir


def uninit_repo(repo_root: Path) -> None:
    repo_root = Path(repo_root)
    muvue_dir = repo_root / ".muvue"
    if not muvue_dir.exists():
        raise NotInitialized(f"{muvue_dir} does not exist")

    manifest_path = muvue_dir / MANIFEST_NAME
    if manifest_path.exists():
        backups: dict[str, str | None] = json.loads(manifest_path.read_text())
        for path_str, original in backups.items():
            path = Path(path_str)
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original)

    import shutil

    shutil.rmtree(muvue_dir)
