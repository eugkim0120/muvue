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

HOOK_NAMES = ["post-commit", "pre-push", "prepare-commit-msg"]
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


def _shim_command(name: str) -> str:
    # prepare-commit-msg needs git's arguments (the message file, its source).
    args = ' "$@"' if name == "prepare-commit-msg" else ""
    return f"{hook_fast_path_command(name)}{args}"


def shim_is_current(content: str, name: str) -> bool:
    """Whether the installed shim block for `name` runs the stdlib fast
    path the way this muvue would. A pre-v4 shim runs the full CLI
    (`-m muvue hook NAME`). A different interpreter path is not outdated:
    a `muvue doctor` run from another environment (`uvx`, say) must not
    repoint shims at itself."""
    begin, end = _hook_marker(name)
    lines = content.splitlines()
    if begin not in lines or end not in lines:
        return False
    block = lines[lines.index(begin) + 1 : lines.index(end)]
    wanted = f"-m muvue._hook {name}" + (' "$@"' if name == "prepare-commit-msg" else "")
    return any(line.rstrip().endswith(wanted) for line in block)


def _install_hook_shim(path: Path, name: str, backups: dict[str, str | None]) -> None:
    key = str(path)
    if key not in backups:
        backups[key] = path.read_text() if path.exists() else None

    begin, end = _hook_marker(name)
    # v4 section 4a (P0.5 hook fast path): every hook shim invokes the
    # stdlib-only `muvue._hook` entry point, not the full Typer CLI
    # (`-m muvue hook`, ~150-400ms cold) -- `-S` skips `site` init too.
    # See src/muvue/_hook.py and docs/decisions.md.
    block = f"{begin}\n{_shim_command(name)}\n{end}\n"

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


PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"


def _register_pre_commit(repo_root: Path, backups: dict[str, str | None]) -> bool:
    """v4 section 5: "register with Husky or pre-commit when present". A
    `repo: local` post-commit hook is appended to `.pre-commit-config.yaml`
    so `pre-commit install -t post-commit` keeps muvue's shim running.

    No YAML library is used (no new runtime dependency), so the file is
    only extended when that is safe as plain text: `repos:` is a block
    list and the last top-level key. Anything else is left untouched and
    the `.git/hooks` shim still runs. `uninit` restores the original
    bytes from the manifest. Returns whether the file was changed."""
    path = repo_root / PRE_COMMIT_CONFIG
    if not path.exists():
        return False
    content = path.read_text()
    begin, end = _gitignore_marker()
    if begin in content:
        return False
    lines = content.splitlines()
    try:
        start = lines.index("repos:")
    except ValueError:
        return False
    rest = [line for line in lines[start + 1:] if line.strip() and not line.lstrip().startswith("#")]
    if any(not (line[0].isspace() or line.startswith("-")) for line in rest):
        return False  # another top-level key follows `repos:`
    items = [line for line in rest if line.lstrip().startswith("- ")]
    indent = items[0][: len(items[0]) - len(items[0].lstrip())] if items else "  "
    entry = json.dumps("env " + hook_fast_path_command("post-commit"))
    block = "\n".join(indent + line for line in (
        begin,
        "- repo: local",
        "  hooks:",
        "    - id: muvue-post-commit",
        "      name: muvue post-commit",
        f"      entry: {entry}",
        "      language: system",
        "      stages: [post-commit]",
        "      always_run: true",
        "      pass_filenames: false",
        end,
    )) + "\n"
    backups.setdefault(str(path), content)
    sep = "" if content.endswith("\n") else "\n"
    path.write_text(content + sep + block)
    return True


GITIGNORE_ENTRIES = [
    ".muvue/muvue.db",
    ".muvue/muvue.db-wal",
    ".muvue/muvue.db-shm",
    ".muvue/session",
    ".muvue/current_node",
    # v4 section 2 file layout: "gitignored; hook fast-path spool
    # (append-only)" -- muvue._hook's queue, see src/muvue/_hook.py.
    ".muvue/queue.jsonl",
    # Drain hand-off file and drainer lock (core/hooks.py drain_queue).
    ".muvue/queue.draining",
    ".muvue/queue.lock",
    # `muvue rebuild --apply` backups.
    ".muvue/muvue.db.bak-*",
    # Runner registry (core/runners.py) and per-node driver logs.
    ".muvue/runners/",
    ".muvue/logs/",
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


def _gitignore_block_span(lines: list[str]) -> tuple[int, int] | None:
    begin, end = _gitignore_marker()
    if begin not in lines or end not in lines:
        return None
    first = lines.index(begin)
    return first, lines.index(end, first)


def gitignore_missing_entries(repo_root: Path) -> list[str]:
    """Entries this muvue ignores that the repository's `.gitignore` doesn't:
    a repository initialised by an older muvue lacks the ones added since."""
    path = Path(repo_root) / ".gitignore"
    lines = set(path.read_text().splitlines()) if path.exists() else set()
    return [e for e in GITIGNORE_ENTRIES if e not in lines]


def _update_gitignore(repo_root: Path, backups: dict[str, str | None]) -> None:
    """Append muvue's marker block, or rewrite an existing one in place
    with the current entries. `uninit` restores the pre-init content
    recorded in `backups`, whichever of the two happened."""
    path = repo_root / ".gitignore"
    key = str(path)
    if key not in backups:
        backups[key] = path.read_text() if path.exists() else None

    begin, end = _gitignore_marker()
    content = path.read_text() if path.exists() else ""
    lines = content.split("\n")
    span = _gitignore_block_span(lines)
    outside = lines if span is None else lines[: span[0]] + lines[span[1] + 1 :]
    # A repo may already ignore one of these as a plain (unmarked) line --
    # don't duplicate it inside the marker block (dogfood-gate follow-up,
    # docs/decisions.md #40). Exact-line match only: substring matching
    # would also skip ".muvue/muvue.db" against ".muvue/muvue.db-wal".
    existing_lines = set(outside)
    block = [begin, *[e for e in GITIGNORE_ENTRIES if e not in existing_lines], end]
    if span is not None:
        path.write_text("\n".join(lines[: span[0]] + block + lines[span[1] + 1 :]))
        return
    sep = "" if content == "" or content.endswith("\n") else "\n"
    path.write_text(content + sep + "\n".join([*block, ""]))


def repair_install(repo_root: Path) -> list[str]:
    """Bring an install from an older muvue up to date: install missing
    hook shims and rewrite the `.gitignore` block. What this changes is
    recorded in the init manifest like `init`'s own changes, so `uninit`
    still restores the repository exactly. Returns what was repaired."""
    repo_root = Path(repo_root)
    manifest_path = repo_root / ".muvue" / MANIFEST_NAME
    backups: dict[str, str | None] = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )
    repaired = []
    husky_dir = repo_root / ".husky"
    for name in HOOK_NAMES:
        path = husky_dir / name if husky_dir.is_dir() else repo_root / ".git" / "hooks" / name
        begin, end = _hook_marker(name)
        if not path.exists() or begin not in path.read_text():
            _install_hook_shim(path, name, backups)
            repaired.append(f"reinstalled hook shim: {path}")
        elif not shim_is_current(path.read_text(), name):
            content = path.read_text()
            backups.setdefault(str(path), _without_muvue_blocks(content))
            lines = content.split("\n")
            first, last = lines.index(begin), lines.index(end)
            path.write_text("\n".join([*lines[: first + 1], _shim_command(name), *lines[last:]]))
            repaired.append(f"upgraded outdated hook shim: {path}")
    missing = gitignore_missing_entries(repo_root)
    if missing:
        _update_gitignore(repo_root, backups)
        repaired.append(f"added to .gitignore: {', '.join(missing)}")
    if repaired:
        manifest_path.write_text(json.dumps(backups, indent=2))
    return repaired


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
    _register_pre_commit(repo_root, backups)
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


def _without_muvue_blocks(text: str) -> str:
    """`text` minus every marker-delimited block muvue added (the
    markers may be indented, as in `.pre-commit-config.yaml`)."""
    kept, inside = [], False
    for line in text.split("\n"):
        marker = line.strip()
        if marker.startswith("# >>> muvue"):
            inside = True
        elif marker.startswith("# <<< muvue") and inside:
            inside = False
        elif not inside:
            kept.append(line)
    return "\n".join(kept)


def _restore(path: Path, original: str | None) -> None:
    """Undo `init` for one file. Unchanged since `init` (apart from
    muvue's own blocks): back to the original bytes, or removed if `init`
    created it. Edited since: only muvue's blocks are taken out, so the
    user's later edits survive `uninit`."""
    if not path.exists():
        if original is not None:
            path.write_text(original)
        return
    remaining = _without_muvue_blocks(path.read_text())

    def meaningful(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip()]

    if original is None:
        if meaningful(remaining) in ([], ["#!/bin/sh"]):
            path.unlink()
        else:
            path.write_text(remaining)
    elif meaningful(remaining) == meaningful(original):
        path.write_text(original)
    else:
        path.write_text(remaining)


def uninit_repo(repo_root: Path) -> None:
    repo_root = Path(repo_root)
    muvue_dir = repo_root / ".muvue"
    if not muvue_dir.exists():
        raise NotInitialized(f"{muvue_dir} does not exist")

    manifest_path = muvue_dir / MANIFEST_NAME
    if manifest_path.exists():
        backups: dict[str, str | None] = json.loads(manifest_path.read_text())
        for path_str, original in backups.items():
            _restore(Path(path_str), original)

    _delete_structure_ref_if_present(repo_root)

    import shutil

    shutil.rmtree(muvue_dir)
