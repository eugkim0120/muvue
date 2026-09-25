"""Who is issuing a verb (plan section 3 `actor_evidence`, section 4
"Honest statement of the boundary").

A same-user agent with shell access can run the CLI, and can obtain a TTY
(`script`, `pty.spawn`), so the human/agent verb split cannot *prevent* an
agent from issuing a human verb. It is detected instead: a verb issued
from a process whose parent chain includes a known agent CLI is recorded
as `actor=agent` with `actor_evidence="agent_parent:<name>"`, flagged in
the dashboard and counted in the rubber-stamp KPI. A human verb from a
process with no TTY is recorded with `actor_evidence="no_tty"`. Detection,
not prevention -- documented as such (docs/threat-model.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Iterable

AGENT_CLI_NAMES = ("claude", "codex", "gemini", "cursor-agent", "aider")
_MAX_DEPTH = 64


class HumanOnly(Exception):
    """A human verb was issued by an actor that isn't a human, and not
    through the detected-agent path (which is allowed and recorded)."""


def require_human(actor: str, actor_evidence: str | None) -> None:
    """Human verbs are never exposed over MCP. At the core boundary they
    accept `actor="human"`, or `actor="agent"` when the CLI detected an
    agent ancestor (`agent_parent:*`): that call is allowed and recorded
    as the agent's, per plan section 4's detection-not-prevention rule."""
    if actor == "human":
        return
    if actor == "agent" and (actor_evidence or "").startswith("agent_parent:"):
        return
    raise HumanOnly(
        f"only a human may perform this action (actor was {actor!r}); "
        "human verbs are never exposed over MCP (plan section 4)"
    )


def _agent_name(argv: list[str]) -> str | None:
    """The known agent CLI an argv belongs to, if any: the executable's
    basename, or for an interpreter (`node .../claude-code/cli.js`) any
    later argument naming one."""
    for arg in argv[:3]:
        base = os.path.basename(arg).lower()
        stem = base.rsplit(".", 1)[0] if base.endswith((".exe", ".js", ".mjs")) else base
        if stem in AGENT_CLI_NAMES:
            return stem
        for name in AGENT_CLI_NAMES:
            if f"/{name}-code/" in arg or f"@anthropic-ai/{name}" in arg:
                return name
    return None


def _proc_parent_and_argv(pid: int) -> tuple[int, list[str]] | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        return ppid, argv
    except (OSError, ValueError, IndexError):
        pass
    try:  # no /proc (macOS)
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        ppid_s, _, command = out.partition(" ")
        return int(ppid_s), command.split()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ancestor_argvs(
    pid: int | None = None, *, lookup: Callable[[int], tuple[int, list[str]] | None] = _proc_parent_and_argv,
) -> Iterable[list[str]]:
    """argv of each ancestor of `pid` (default: this process), nearest
    first, stopping at init."""
    info = lookup(pid if pid is not None else os.getpid())
    depth = 0
    while info is not None and depth < _MAX_DEPTH:
        ppid, _ = info
        if ppid <= 1:
            return
        info = lookup(ppid)
        if info is None:
            return
        yield info[1]
        depth += 1


def detect_invoker(
    *, isatty: Callable[[], bool] | None = None, ancestors: Iterable[list[str]] | None = None,
) -> tuple[str, str]:
    """`(actor, actor_evidence)` for a CLI human verb."""
    for argv in ancestors if ancestors is not None else ancestor_argvs():
        name = _agent_name(argv)
        if name is not None:
            return "agent", f"agent_parent:{name}"
    tty = isatty() if isatty is not None else sys.stdin.isatty()
    return "human", "tty" if tty else "no_tty"
