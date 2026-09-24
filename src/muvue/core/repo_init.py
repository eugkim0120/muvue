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


def _muvue_src_dir() -> Path:
    """Directory that must be on `sys.path` for `import muvue._hook` to
    resolve when the interpreter is invoked with `-S` (v4 section 4a):
    `-S` skips `site`'s own sys.path manipulation (`.pth`/editable-
    install processing), which is normally what makes an installed
    `muvue` importable at all, not just what makes Typer/Pydantic slow
    to import -- discovered empirically while wiring this phase (see
    docs/decisions.md). `muvue.__file__`'s grandparent directory covers
    both layouts: a `src/`-layout editable install (this repo's own dev
    venv: `<repo>/src/muvue/__init__.py` -> `<repo>/src`) and a normal
    site-packages install (`<site-packages>/muvue/__init__.py` ->
    `<site-packages>`)."""
    import muvue as _muvue_pkg

    return Path(_muvue_pkg.__file__).resolve().parent.parent


def hook_fast_path_command(name: str) -> str:
    """v4 section 4a (P0.5 hook fast path): the shared command string
    every hook shim invokes -- git hooks here, and the Claude Code
    adapter's `.claude/settings.json` entries in `core/adapters.py`
    (single source of truth so both shim writers stay in sync).
    `PYTHONPATH=<src dir>` prefixed so `-S` (skip `site` init, part of
    the latency win -- see `src/muvue/_hook.py`) doesn't also break
    finding the `muvue` package itself; PYTHONPATH is still honored
    with `-S` (it's applied before `site` would run, not by it)."""
    py = shlex.quote(sys.executable)
    pythonpath = shlex.quote(str(_muvue_src_dir()))
    return f"PYTHONPATH={pythonpath} {py} -S -m muvue._hook {name}"

# `init --sandbox` (plan section 5: "`init --sandbox` emits a compose file
# for container isolation (later)"). Scaffold only -- muvue does not build,
# start, or manage this stack; strict mode's real isolation today is the
# git-worktree/airlock mechanism in core/strict.py, not this file. See
# docs/decisions.md.
SANDBOX_COMPOSE = """\
# muvue `init --sandbox` scaffold (plan section 5, P4: "(later)").
#
# NOT IMPLEMENTED: muvue does not build, start, stop, or otherwise manage
# this compose stack. Running node work inside a container is future work.
# Today's strict-mode isolation is the per-node git worktree bound at
# `start` (see core/strict.py) -- this file is a documented placeholder
# for the container-isolation layer described in the handoff plan.
version: "3.8"
services:
  muvue-sandbox:
    build: .
    volumes:
      - .:/workspace
    working_dir: /workspace
    command: ["sleep", "infinity"]
"""


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
    # v4 section 4a (P0.5 hook fast path): every hook shim invokes the
    # stdlib-only `muvue._hook` entry point, not the full Typer CLI
    # (`-m muvue hook`, ~150-400ms cold) -- `-S` skips `site` init too.
    # See src/muvue/_hook.py and docs/decisions.md.
    body = f"{hook_fast_path_command(name)}\n"
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
        # v4 section 2 file layout: "gitignored; hook fast-path spool
        # (append-only)" -- muvue._hook's queue, see src/muvue/_hook.py.
        ".muvue/queue.jsonl",
        # Not listed in plan section 2's committed-files table (only
        # config.toml/components.json/decisions.json are meant to be
        # committed) -- gitignored so an ordinary `git add -A` mid-project
        # never sweeps it into a commit. It must stay recoverable purely
        # from the live filesystem for `uninit_repo` to restore hook shims
        # correctly; a later `git reset`/checkout that a commit containing
        # it would be exposed to could otherwise delete or stale it. Found
        # via the v4 P0 filesystem-snapshot round-trip test (see
        # tests/test_init_uninit.py, docs/decisions.md).
        ".muvue/.init_manifest.json",
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


def init_repo(repo_root: Path, *, sandbox: bool = False) -> Path:
    """Scaffold .muvue/ in repo_root. Returns the .muvue directory path.

    `sandbox=True` additionally writes a documented, unimplemented-runtime
    compose scaffold (`.muvue/sandbox-compose.yml`, plan section 5's
    "`init --sandbox` ... (later)"). It needs no manifest/uninit handling
    of its own: it lives inside `.muvue/`, which `uninit_repo` already
    removes wholesale."""
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
    if sandbox:
        (muvue_dir / "sandbox-compose.yml").write_text(SANDBOX_COMPOSE)

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


def _delete_structure_ref_if_present(repo_root: Path) -> None:
    """v4 section 9 created a `muvue/structure` git ref (`close.STRUCTURE_REF`)
    that lives entirely inside `.git/refs/` -- `uninit`'s manifest-restore
    above never touches it, since it was never a file `init` wrote or
    modified. v4's own P0 acceptance wording predates section 9 (P0 comes
    before P6 in the plan's phase ordering) and doesn't mention this ref's
    uninit lifecycle either way. Judgment call (docs/decisions.md): a
    `muvue/structure` ref is muvue-owned the same way `.muvue/` is, so
    "no muvue-owned tracked or untracked files [or refs] remain" extends
    to it -- `uninit` deletes it if present, via `git update-ref -d`
    (never touches the working tree, index or HEAD, same guarantee
    `close._write_structure_commit` relies on). Skipped, not force-deleted,
    if it's the ref currently checked out (`git symbolic-ref HEAD`) --
    an edge case no normal workflow reaches, but deleting a ref out from
    under the checked-out HEAD is a needless footgun to add here.

    Imported lazily (not at module scope) to keep `init_repo`'s own import
    surface small and avoid any import-order coupling to `core/close.py`."""
    import subprocess

    from . import close as close_mod

    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return

    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if symbolic.returncode == 0 and symbolic.stdout.strip() == close_mod.STRUCTURE_REF:
        return

    subprocess.run(
        ["git", "update-ref", "-d", close_mod.STRUCTURE_REF],
        cwd=repo_root, capture_output=True, text=True,
    )


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

    _delete_structure_ref_if_present(repo_root)

    import shutil

    shutil.rmtree(muvue_dir)
