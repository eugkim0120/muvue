"""Stdlib-only hot-path entry point for git/agent hook shims (v4 handoff
plan section 4a: "the 50 ms budget was unachievable with Typer +
Pydantic on a cold interpreter, and PreToolUse fires per tool call").

Invoked as `<abs-python> -S -m muvue._hook NAME [PATH]` by the shim
files `core/repo_init.py` (git hooks) and `core/adapters.py` (the
Claude Code adapter's `.claude/settings.json` hook commands) write.
`-S` skips `site` module initialization; this module itself imports
**only** `sys`, `os`, `json`, `time` at module scope, and `sqlite3`
lazily inside the one path that needs it. It must never import
anything from `muvue` itself beyond the bare (empty) `muvue/__init__.py`
package init, and never `muvue.core` or any of its submodules --
`muvue/core/__init__.py` eagerly imports every core module including
`core/config.py`, which imports Typer/Pydantic transitively. See
`tests/test_hook_import_graph.py` (proves the transitive closure, not
just this file's own import statements) and docs/decisions.md #79.

Every hook event except `PreToolUse` does the minimum possible amount
of work: append one JSON line describing the event to
`.muvue/queue.jsonl` (gitignored, append-only spool) and exit. All the
real processing those events used to do synchronously (parsing commit
trailers, linking commits, blocking on unlogged in-progress work, ...)
now happens later, when something drains the queue --
`core.hooks.drain_queue`, a normal `muvue.core` module that runs
through `write_txn` as usual (working rule 3: the queue *append* here
is intentionally NOT a core DB write, by design, to stay off SQLite on
the hot path; the *drain* is a core write like any other mutation).

`PreToolUse` is the one exception (v4 section 4a): it must answer
allow/deny, so it is the only event permitted to read the DB. It opens
`.muvue/muvue.db` read-only, runs a single indexed query, and has a
hard 150ms wall-clock deadline after which it fails open (allows the
tool call) and spools a `hook_timeout` line so the miss is still
visible later (v4 principle 10: "detection everywhere else").
"""

from __future__ import annotations

import json
import os
import sys
import time

QUEUE_RELPATH = os.path.join(".muvue", "queue.jsonl")
CURRENT_NODE_RELPATH = os.path.join(".muvue", "current_node")
DB_RELPATH = os.path.join(".muvue", "muvue.db")

# v4 section 4a: "a hard 150 ms deadline after which it fails open".
PRE_TOOL_USE_DEADLINE_S = 0.150

# v4 section 7 / changelog item 10: `Bash` used to be here too, for the
# `git commit`-without-trailer string-match block -- REMOVED, not
# fixed, in favor of post-hoc `post-commit` detection (see
# `_decide`'s old Bash branch, now gone, and docs/decisions.md #100).
# With no decision logic left for `Bash`, it no longer needs the DB at
# all -- dropping it here (not just short-circuiting inside `_decide`)
# keeps every `Bash` PreToolUse call on the zero-DB-open fast path,
# same as Read/Grep/Glob/etc.
_BLOCKING_TOOL_NAMES = ("Edit", "Write")


def _now_iso() -> str:
    """`%Y-%m-%dT%H:%M:%S.mmmZ`, stdlib-`time`-only (no `datetime`) --
    close enough to the rest of the codebase's timestamp convention
    (`core.events`/`core.daemon` use sqlite's own millisecond-precision
    `strftime`); this value is metadata inside the spooled line, not a
    DB column, so an exact-format round-trip isn't load-bearing."""
    t = time.time()
    ms = int((t % 1) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{ms:03d}Z"


def _find_repo_root(start: str) -> str | None:
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".muvue")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _append_queue(repo_root: str, event: dict) -> None:
    path = os.path.join(repo_root, QUEUE_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _get_current_node(repo_root: str) -> int | None:
    """Inlined equivalent of `core.adapters.get_current_node` -- not
    imported from there, since even that (otherwise stdlib-only) module
    lives under `muvue.core`, and `import muvue.core.anything` runs
    `muvue/core/__init__.py`, which pulls in Typer/Pydantic. Duplicated
    on purpose; see module docstring."""
    path = os.path.join(repo_root, CURRENT_NODE_RELPATH)
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _read_head_sha(repo_root: str) -> str | None:
    """Resolve HEAD's commit sha by reading `.git` directly (no
    `subprocess` -- not on this module's stdlib allow-list, and
    spawning `git` would undo the whole point of the fast path).
    Handles a loose ref and the `packed-refs` fallback; a detached HEAD
    (`.git/HEAD` holding a raw sha, not a `ref: ...` line) is returned
    as-is."""
    git_dir_env = os.environ.get("GIT_DIR")
    if git_dir_env:
        git_dir = git_dir_env if os.path.isabs(git_dir_env) else os.path.join(repo_root, git_dir_env)
    else:
        git_dir = os.path.join(repo_root, ".git")
    try:
        with open(os.path.join(git_dir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head.split(" ", 1)[1].strip()
    ref_path = os.path.join(git_dir, ref)
    try:
        with open(ref_path) as f:
            sha = f.read().strip()
            if sha:
                return sha
    except OSError:
        pass
    packed_path = os.path.join(git_dir, "packed-refs")
    try:
        with open(packed_path) as f:
            for line in f:
                line = line.strip()
                if line.endswith(" " + ref):
                    return line.split()[0]
    except OSError:
        return None
    return None


def pre_tool_use(
    repo_root: str,
    payload: dict,
    *,
    clock=time.monotonic,
    deadline_s: float = PRE_TOOL_USE_DEADLINE_S,
) -> dict:
    """The one DB-reading path (v4 section 4a). Mirrors
    `core.claude_hooks.pre_tool_use`'s decision logic exactly (that
    logic does not change, only its execution path does), against a
    read-only connection, under a hard wall-clock deadline enforced by
    an elapsed-time check after the query -- a pathologically slow
    query still can't be preempted mid-flight, but it can't make this
    process block past the deadline either: on overrun this fails open
    and still records `hook_timeout` to the queue so the miss is
    auditable."""
    start = clock()
    tool_name = payload.get("tool_name", "")
    node_id = payload.get("node_id")
    if node_id is None:
        node_id = _get_current_node(repo_root)

    if tool_name not in _BLOCKING_TOOL_NAMES:
        # Most PreToolUse calls (Read/Grep/Glob/...) never need the DB
        # at all -- keep them on the zero-import-cost path.
        return {"decision": "allow"}

    db_path = os.path.join(repo_root, DB_RELPATH)
    if not os.path.exists(db_path):
        return {"decision": "allow"}

    import sqlite3  # lazy: only this branch pays for it (v4 section 4a)

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        decision = _decide(conn, tool_name=tool_name, node_id=node_id)
    finally:
        if conn is not None:
            conn.close()

    if clock() - start > deadline_s:
        _append_queue(
            repo_root,
            {"event": "hook_timeout", "ts": _now_iso(), "hook": "pre-tool-use", "node_id": node_id},
        )
        return {"decision": "allow"}
    return decision


def _decide(conn, *, tool_name: str, node_id) -> dict:
    # `tool_name` is already filtered to `_BLOCKING_TOOL_NAMES` (Edit/Write
    # only, as of v4 section 7 -- see that tuple's docstring) by the one
    # caller, `pre_tool_use`, before this ever runs.
    if node_id is None:
        return {
            "decision": "block",
            "reason": "no active muvue node -- run `muvue start NODE_ID --owner ...` first",
        }
    row = conn.execute(
        "SELECT status FROM nodes WHERE id = ? AND deleted_at IS NULL", (node_id,)
    ).fetchone()
    if row is None:
        return {"decision": "block", "reason": f"no such muvue node: {node_id}"}
    if row["status"] == "awaiting_approval":
        return {"decision": "block", "reason": f"node {node_id} is awaiting_approval"}
    if row["status"] != "in_progress":
        return {
            "decision": "block",
            "reason": f"node {node_id} is {row['status']!r}, not in_progress",
        }
    return {"decision": "allow"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 0
    name = argv[0]
    path = argv[1] if len(argv) > 1 else "."
    repo_root = _find_repo_root(path)
    if repo_root is None:
        # No .muvue/ found -- nothing to spool against, nothing to
        # decide. Fail open silently rather than raise: a hook shim
        # left behind in a repo that later ran `muvue uninit` must not
        # break every tool call.
        return 0

    if name == "pre-tool-use":
        payload = _read_stdin_json()
        result = pre_tool_use(repo_root, payload)
        sys.stdout.write(json.dumps(result) + "\n")
        return 2 if result.get("decision") == "block" else 0

    if name == "post-commit":
        sha = _read_head_sha(repo_root)
        _append_queue(repo_root, {"event": "post-commit", "ts": _now_iso(), "sha": sha})
        return 0

    if name in ("session-start", "pre-compact", "stop"):
        payload = _read_stdin_json()
        node_id = payload.get("node_id")
        if node_id is None:
            node_id = _get_current_node(repo_root)
        event = {"event": name, "ts": _now_iso(), "node_id": node_id}
        if name == "pre-compact":
            event["summary"] = payload.get("summary")
        _append_queue(repo_root, event)
        sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
        return 0

    if name == "pre-push":
        _append_queue(repo_root, {"event": "pre-push", "ts": _now_iso()})
        return 0

    # Unknown/unsupported event name (e.g. `pre-receive`, which runs
    # server-side in a bare airlock repo with no `.muvue/` -- it is
    # never reached via this branch, and stays on the full `muvue hook
    # pre-receive` CLI path; see docs/decisions.md). Fail open.
    return 0


if __name__ == "__main__":
    sys.exit(main())
