"""v4 section 4a / P0.5 acceptance #2: "`muvue._hook` imports no
third-party module", asserted by an import-graph test, not a grep of
`_hook.py`'s own import statements. A grep only proves this file's
own imports are clean today; it says nothing about the *transitive*
closure, and a later edit could import something from `muvue.core`
(which imports Typer/Pydantic/FastAPI via `core/config.py`) without
this file's own `import` lines ever mentioning them directly (e.g. by
adding `from muvue.core import adapters` for "just this one helper").
This test spawns a real subprocess, imports only `muvue._hook`, and
inspects the resulting `sys.modules` -- the actual thing that
happened, not a static approximation of it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

FORBIDDEN_SUBSTRINGS = (
    "typer",
    "pydantic",
    "fastapi",
    "starlette",
    "uvicorn",
    "muvue.core",
    "muvue.cli",
    "muvue.api",
    "muvue.mcp_server",
)


def _imported_modules_after_importing_hook() -> list[str]:
    code = (
        "import sys, json\n"
        "import muvue._hook\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=SRC_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def test_importing_muvue_hook_never_pulls_in_a_forbidden_module():
    modules = _imported_modules_after_importing_hook()
    offenders = [
        m for m in modules if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_SUBSTRINGS)
    ]
    assert offenders == [], f"muvue._hook transitively imported: {offenders}"


def test_importing_muvue_hook_only_pulls_in_expected_stdlib_plus_package_init():
    """Positive-side check, not just an absence proof: the modules that
    *are* new after `import muvue._hook` are exactly `muvue`,
    `muvue._hook`, and stdlib. Catches an accidental third-party import
    under a name not in `FORBIDDEN_SUBSTRINGS` (a future dependency this
    test's fixed denylist doesn't yet know about)."""
    code = (
        "import sys, json\n"
        "before = sorted(sys.modules)\n"
        "import muvue._hook\n"
        "after = sorted(sys.modules)\n"
        "print(json.dumps(sorted(set(after) - set(before))))\n"
    )
    out = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=SRC_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    newly_imported = json.loads(out.stdout)
    stdlib_paths = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    for mod in newly_imported:
        top = mod.split(".")[0]
        if top == "muvue":
            continue
        # Every remaining top-level module must be stdlib.
        if stdlib_paths is not None:
            assert top in stdlib_paths, f"non-stdlib module imported by muvue._hook: {mod}"


def test_hook_module_never_imports_sqlite3_at_top_level():
    """v4 section 4a: "`sqlite3` lazily" -- most invocations (every hook
    except PreToolUse) never touch the DB, so `sqlite3` must not be
    among the modules imported just by `import muvue._hook` itself."""
    modules = _imported_modules_after_importing_hook()
    assert "sqlite3" not in modules
