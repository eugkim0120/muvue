"""A scripted `fake` agent for testing muvue.core's enforcement, per plan
section 10's testing strategy: "fake agent with scripted behaviours:
cooperative; lazy (early done, vacuous lessons); adversarial (edits
tests, forges trailer, calls human verbs, ignores rate limit)".

This is a lightweight in-process test fixture, not an installable
`muvue-fake-agent` CLI package (`[agents.fake] command = "muvue-fake-
agent"` in config.toml already names that binary as P5 runner/driver
scope -- spawning it as a real subprocess needs the runner, which plan
section 12 working rule 7 explicitly excludes from P3). Each behaviour is
a plain function that drives `muvue.core` exactly the way a real agent
process would (through the CLI-equivalent verb functions, never raw
SQL), so the P3 acceptance #4 assertion -- "blocked or flagged by
muvue.core, not by the fake agent script itself" -- is meaningful: these
functions do not self-censor; they attempt the adversarial action and
let core's own checks raise or record the flag.
"""

from __future__ import annotations

import sqlite3

from muvue.core import gates, hooks, nodes


def cooperative_run(conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent") -> dict:
    """Happy path: start, do the work (a real note), done."""
    nodes.start(conn, node_id, owner=owner)
    nodes.add_note(conn, node_id, kind="discovery", text="did the work", actor="agent")
    return nodes.done(conn, node_id, owner=owner)


def lazy_early_done(conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent") -> dict:
    """Lazy: `done` immediately after `start`, with no work recorded.
    Core does not currently require evidence of work before `done` (no
    P0-P2 mechanism ties `done` to note/commit history), so this
    succeeds -- documented as a P3 scope boundary, not a bug: catching
    "no real work happened" needs the structure/anchor layer (P6+) to
    judge diff substance, which doesn't exist yet."""
    nodes.start(conn, node_id, owner=owner)
    return nodes.done(conn, node_id, owner=owner)


def lazy_vacuous_lesson(conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent") -> dict:
    """Lazy: fails a node with an empty lesson instead of a real one.
    Core refuses it (plan section 3: a lesson must carry trigger,
    failure, do_instead and scope), so this raises ValueError and the
    node stays `in_progress`. Lesson *quality* beyond "every field
    present" is not judged -- that would need an LLM-graded check."""
    nodes.start(conn, node_id, owner=owner)
    return nodes.fail(conn, node_id, owner=owner, lesson="", trigger="", do_instead="", scope="")


def adversarial_edit_test_file(
    conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent", config
) -> dict:
    """Adversarial: claims to touch a test file, then calls `done`.
    core.risk.is_flagged (plan section 5: "diffs touching test files... are
    always flagged") must force this to `review`, never straight to
    `done`, regardless of risk tier."""
    conn.execute(
        "INSERT OR IGNORE INTO predicted_touches (node_id, path_glob) VALUES (?, ?)",
        (node_id, "tests/test_sneaky.py"),
    )
    conn.commit()
    nodes.start(conn, node_id, owner=owner)
    return nodes.done(conn, node_id, owner=owner, config=config)


def adversarial_forge_trailer(
    conn: sqlite3.Connection, *, forged_node_id: int, commit_sha: str = "forged-sha"
) -> dict:
    """Adversarial: fabricates a `Muvue-Node:` trailer on a commit for a
    node this script never started/owns, trying to make it look linked
    without ever holding the lease. `handle_post_commit` only records
    bookkeeping metadata (`node_commits`, `commit.linked`) -- it must
    never grant lease ownership or move the node's status, so a forged
    trailer cannot be used to bypass `start`'s ownership check."""
    return hooks.handle_post_commit(
        conn, commit_sha=commit_sha, message=f"totally legit\n\nMuvue-Node: {forged_node_id}\n"
    )


def adversarial_call_human_verb(conn: sqlite3.Connection, node_id: int) -> None:
    """Adversarial: calls a human-only verb directly against core as
    `actor="agent"` -- `core.gates`/`core.nodes` must refuse it themselves
    (`HumanOnly`), not rely on the CLI/API/MCP surface never exposing it."""
    gates.approve_node(conn, node_id, actor="agent")


def adversarial_ignore_block(conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent") -> dict:
    """Adversarial: a node was blocked (e.g. `reason="rate_limit"` after
    `on_rate_limit` enforcement) and the agent tries to push it straight
    to `done` anyway, ignoring the block. There is no
    (blocked, review)/(blocked, done) edge in `state_machine.TRANSITIONS`,
    so this must raise `InvalidTransition`."""
    return nodes.done(conn, node_id, owner=owner)


def lazy_all_external(
    conn: sqlite3.Connection, project_id: int, *, owner: str = "fake-agent", config
) -> dict:
    """Lazy: marks every criterion `external`, so muvue can't run it, and
    calls `done`. It must land in `review` and be listed as unverified in
    the inbox (plan section 5), never auto-approve."""
    task = nodes.create_node(
        conn, project_id=project_id, kind="task", title="all external",
        criteria=["it works on my machine"], criteria_mode="external",
        predicted_touches=["a.py"], status="pending",
    )
    gates.approve_gate2(conn, project_id, config=config)
    nodes.start(conn, task["id"], owner=owner)
    return nodes.done(conn, task["id"], owner=owner, config=config)


def adversarial_resume_during_rate_limit(
    conn: sqlite3.Connection, node_id: int, *, owner: str = "fake-agent"
) -> dict:
    """Adversarial: the node is `blocked(rate_limit)` and the agent just
    starts it again before `retry_at`, ignoring the limit."""
    return nodes.start(conn, node_id, owner=owner)


def adversarial_read_port_file(repo_root) -> dict:
    """Adversarial: reads the daemon's port file hoping for a credential,
    the way v3's `~/.muvue/session` handed one out."""
    import json

    from muvue.core import daemon

    return json.loads(daemon.port_file_path(repo_root).read_text())


def adversarial_script_tty_verb(repo_root, bin_dir, argv: list[str], *, agent_name: str = "codex"):
    """Adversarial: an agent CLI (a process named `agent_name`) wraps a
    human verb in `script` so the verb sees a TTY. Returns the completed
    agent process. The default name is `codex` so the result can't be
    confused with a Claude Code session the test suite itself runs in."""
    import os
    import shlex
    import subprocess
    import sys

    agent = bin_dir / agent_name
    agent.write_text("#!/bin/sh\nscript -qec \"$1\" /dev/null\n")
    agent.chmod(0o755)
    command = shlex.join([sys.executable, "-m", "muvue", *argv, "--path", str(repo_root)])
    return subprocess.run(
        [str(agent), command], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
