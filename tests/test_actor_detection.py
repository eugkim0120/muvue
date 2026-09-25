"""Plan section 4: human verbs from an agent's process tree are recorded
as `actor=agent`; human verbs without a TTY are visible as `no_tty`."""

from __future__ import annotations

import pytest

from muvue.core import actor


def test_agent_parent_is_detected_through_the_chain():
    chain = [["bash"], ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], ["sshd"]]
    assert actor.detect_invoker(isatty=lambda: True, ancestors=chain) == (
        "agent", "agent_parent:claude",
    )


@pytest.mark.parametrize("argv,name", [
    (["/home/u/.local/bin/claude", "-p"], "claude"),
    (["codex", "exec", "--json"], "codex"),
    (["/opt/bin/gemini"], "gemini"),
    (["cursor-agent"], "cursor-agent"),
    (["/x/claude-code/bin/claude.exe"], "claude"),
])
def test_known_agent_clis(argv, name):
    assert actor._agent_name(argv) == name


@pytest.mark.parametrize("argv", [["bash"], ["python", "claude_notes.py"], ["vim", "codex.md"]])
def test_ordinary_processes_are_not_agents(argv):
    assert actor._agent_name(argv) is None


def test_no_agent_parent_with_tty_is_a_human():
    assert actor.detect_invoker(isatty=lambda: True, ancestors=[["bash"], ["tmux"]]) == ("human", "tty")


def test_no_tty_is_visible():
    """"a human verb issued by a non-TTY process is visible rather than
    merely disallowed" (plan section 3)."""
    assert actor.detect_invoker(isatty=lambda: False, ancestors=[["cron"]]) == ("human", "no_tty")


def test_script_obtained_tty_under_an_agent_is_still_caught():
    """Adversarial: the agent wraps the call in `script` to get a TTY. The
    TTY is real, but `claude` is still in the parent chain."""
    chain = [["script", "-qc", "muvue approve review:3"], ["bash"], ["claude"]]
    assert actor.detect_invoker(isatty=lambda: True, ancestors=chain)[0] == "agent"


def test_ancestor_walk_uses_the_parent_links():
    table = {10: (9, ["muvue"]), 9: (8, ["bash"]), 8: (1, ["claude"])}
    assert list(actor.ancestor_argvs(10, lookup=table.get)) == [["bash"], ["claude"]]


def test_require_human_accepts_detected_agent_calls_and_refuses_plain_agents():
    actor.require_human("human", "tty")
    actor.require_human("agent", "agent_parent:codex")
    with pytest.raises(actor.HumanOnly):
        actor.require_human("agent", "mcp")
    with pytest.raises(actor.HumanOnly):
        actor.require_human("agent", None)
