"""v4 section 1, principle 2: "`core` exposes exactly two context
managers, `read_txn()` and `write_txn()` ... No raw `conn.execute`
outside them." Enforced statically: every `.execute`/`.executemany`/
`.executescript` call in the package must sit lexically inside a
`with read_txn(...)`/`with write_txn(...)` block.

Exempt: `core/db.py` (defines the context managers and the connection
pragmas), `core/migrate.py` (DDL on a DB being upgraded), `muvue/_hook.py`
(the stdlib-only fast path, which must not import `muvue.core`), and
`PRAGMA` statements, which configure or inspect a connection rather than
read or write state.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "muvue"
EXEMPT_FILES = {"core/db.py", "core/migrate.py", "_hook.py"}
TXN_NAMES = {"read_txn", "write_txn"}
EXEC_ATTRS = {"execute", "executemany", "executescript"}


def _is_txn_with(node: ast.With) -> bool:
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call):
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in TXN_NAMES:
                return True
    return False


def _is_pragma(call: ast.Call) -> bool:
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.lstrip().upper().startswith("PRAGMA")
    if isinstance(first, ast.JoinedStr) and first.values:
        head = first.values[0]
        return isinstance(head, ast.Constant) and str(head.value).lstrip().upper().startswith("PRAGMA")
    return False


def _violations() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in EXEMPT_FILES:
            continue
        tree = ast.parse(path.read_text())

        def visit(node, inside: bool):
            if isinstance(node, ast.With) and _is_txn_with(node):
                inside = True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in EXEC_ATTRS
                and not inside
                and not _is_pragma(node)
            ):
                found.append(f"{rel}:{node.lineno}")
            for child in ast.iter_child_nodes(node):
                visit(child, inside)

        visit(tree, False)
    return found


def test_no_raw_execute_outside_a_transaction_context_manager():
    violations = _violations()
    assert violations == [], (
        f"{len(violations)} raw execute call(s) outside read_txn/write_txn:\n"
        + "\n".join(violations)
    )
