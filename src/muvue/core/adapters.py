"""Adapter config writers (plan section 7): `muvue adapter install
<name>` points a vendor coding tool at muvue's CLI/MCP surface.

Claude Code gets a real hook config (`.claude/settings.json`) wired to
`muvue hook <event>` handlers (see core/claude_hooks.py) -- SessionStart,
PreToolUse, PreCompact, Stop. Codex/Gemini/Cursor are config writers only
(plan section 7: "Codex, Gemini, Cursor: config writers"): each gets a
small instructions/config file pointing at `muvue brief`/`muvue mcp`,
written in this tool's best-effort guess at that vendor's config format
-- **not verified against live vendor docs** (no network access in this
environment; see docs/decisions.md and docs/providers.md). Every
adapter's config embeds `protocol_version`; `core.doctor` warns if an
installed adapter's embedded version doesn't match the repo's current
`config.toml` `protocol_version` (plan section 7: "doctor warns on
mismatch").

None of this touches `.muvue/muvue.db` -- these are file writes only
(the same category of side effect `core.repo_init`'s hook shims already
are), never raw SQL, consistent with working rule 3.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

CURRENT_NODE_RELPATH = ".muvue/current_node"


# --------------------------------------------------------------------------
# "Current node" pointer: the file-based bridge between `muvue start` and
# the Claude Code adapter's hooks, which have no other way to know which
# muvue node a given editor session is working on (light mode has no
# worktree-per-node isolation -- that's P4 strict mode).
# --------------------------------------------------------------------------


def set_current_node(repo_root: Path, node_id: int) -> None:
    path = Path(repo_root) / CURRENT_NODE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(node_id))


def get_current_node(repo_root: Path) -> int | None:
    path = Path(repo_root) / CURRENT_NODE_RELPATH
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def clear_current_node(repo_root: Path) -> None:
    path = Path(repo_root) / CURRENT_NODE_RELPATH
    path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------

_CLAUDE_HOOK_EVENTS = {
    "SessionStart": "session-start",
    "PreToolUse": "pre-tool-use",
    "PreCompact": "pre-compact",
    "Stop": "stop",
}


def _hook_command() -> str:
    py = shlex.quote(sys.executable)
    return f"{py} -m muvue hook"


def install_claude_code(repo_root: Path, *, protocol_version: int) -> Path:
    """Write/merge `.claude/settings.json`'s `hooks` section. Merges into
    an existing settings.json (never overwrites unrelated keys or other
    tools' hook entries -- plan section 5's shim principle, "append to
    existing hooks", applied to this JSON config instead of a shell
    script) and is idempotent: re-running replaces only muvue's own
    entries, detected by the `_muvue` marker each hook command carries."""
    repo_root = Path(repo_root)
    path = repo_root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})

    base_cmd = _hook_command()
    for claude_event, muvue_name in _CLAUDE_HOOK_EVENTS.items():
        command = f"{base_cmd} {muvue_name}"
        entry_list = hooks.get(claude_event, [])
        # Idempotent + non-destructive: drop any previous muvue entry for
        # this event (identified by the command containing "-m muvue
        # hook"), keep every other tool's entries, then re-add ours.
        entry_list = [
            group
            for group in entry_list
            if not any("-m muvue hook" in h.get("command", "") for h in group.get("hooks", []))
        ]
        group: dict = {"hooks": [{"type": "command", "command": command}]}
        if claude_event == "PreToolUse":
            group["matcher"] = "Edit|Write|Bash"
        entry_list.append(group)
        hooks[claude_event] = entry_list

    settings["_muvue"] = {"protocol_version": protocol_version}
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def claude_code_protocol_version(repo_root: Path) -> int | None:
    path = Path(repo_root) / ".claude" / "settings.json"
    if not path.exists():
        return None
    try:
        settings = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return settings.get("_muvue", {}).get("protocol_version")


# --------------------------------------------------------------------------
# Codex, Gemini, Cursor: config writers only (best-effort, unverified --
# see module docstring).
# --------------------------------------------------------------------------


def install_codex(repo_root: Path, *, protocol_version: int) -> Path:
    """Codex CLI reads project instructions from `AGENTS.md` at the repo
    root (per Codex's documented convention at the time this was
    written); append a muvue section pointing at the CLI/MCP surface."""
    return _install_markdown_instructions(
        Path(repo_root) / "AGENTS.md", vendor="codex", protocol_version=protocol_version
    )


def install_gemini(repo_root: Path, *, protocol_version: int) -> Path:
    """Gemini CLI reads project instructions from `GEMINI.md`."""
    return _install_markdown_instructions(
        Path(repo_root) / "GEMINI.md", vendor="gemini", protocol_version=protocol_version
    )


def install_cursor(repo_root: Path, *, protocol_version: int) -> Path:
    """Cursor reads project rules from `.cursor/rules/*.mdc`."""
    path = Path(repo_root) / ".cursor" / "rules" / "muvue.mdc"
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"<!-- muvue protocol_version={protocol_version} -->"
    body = _instructions_body("cursor", protocol_version)
    path.write_text(f"{marker}\n---\ndescription: muvue task protocol\nalwaysApply: true\n---\n\n{body}\n")
    return path


def _instructions_body(vendor: str, protocol_version: int) -> str:
    return (
        f"# muvue ({vendor} adapter, protocol_version={protocol_version})\n\n"
        "This repo is managed by muvue (light mode). Before editing code:\n\n"
        "1. Run `muvue brief NODE_ID` to see the task you've been assigned.\n"
        "2. Run `muvue start NODE_ID --owner <your-agent-id>` to take the lease.\n"
        "3. Record discoveries/decisions with `muvue note NODE_ID --text ...`.\n"
        "4. Finish with `muvue done NODE_ID --owner <your-agent-id> --summary ...`.\n"
        "5. Every commit message must include a `Muvue-Node: NODE_ID` trailer.\n\n"
        "If an MCP client is available, prefer the `muvue mcp` server over the "
        "raw CLI -- it exposes the same agent verbs "
        "(brief/show/start/done/fail/note/ask/wait/replan/status) and nothing "
        "else (human verbs are never exposed over MCP).\n"
    )


def _install_markdown_instructions(path: Path, *, vendor: str, protocol_version: int) -> Path:
    begin = f"<!-- >>> muvue ({vendor}) >>> -->"
    end = f"<!-- <<< muvue ({vendor}) <<< -->"
    block = f"{begin}\n{_instructions_body(vendor, protocol_version)}{end}\n"
    if path.exists():
        content = path.read_text()
        if begin in content:
            import re

            content = re.sub(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", block, content, flags=re.DOTALL)
        else:
            sep = "" if content.endswith("\n") or content == "" else "\n"
            content = content + sep + "\n" + block
    else:
        content = block
    path.write_text(content)
    return path
