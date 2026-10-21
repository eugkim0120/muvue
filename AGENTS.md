# AGENTS.md

Instructions for any coding agent (Claude Code, OpenCode, Codex, ...) working in this repository.

## Purpose

muvue is a CLI and live dashboard for working with AI coding agents through a flow diagram of the plan
(spec, tasks, acceptance criteria, approval gates) instead of a chat transcript. State lives in a
per-repository SQLite database under `.muvue/`. See `README.md` for the user-facing tour.

## Layout

- `src/muvue/core/` - the state machine and everything behind the commands (nodes, gates, runner,
  drivers, daemon, hooks, sessions). Covered by the 85% coverage gate.
- `src/muvue/cli/main.py` - the Typer CLI (`muvue` entry point).
- `src/muvue/api/` - the FastAPI daemon behind `muvue serve`; `api/static/index.html` is the built
  dashboard page, committed.
- `src/muvue/mcp_server.py`, `src/muvue/_hook.py` - MCP stdio server and the stdlib-only hook fast path.
- `dashboard/` - Preact + Vite source of the dashboard page (vitest tests in `dashboard/test/`).
- `vscode-extension/` - the VS Code extension (webview + command bridge).
- `bench/` - benchmark runner, scoring and suites; tested from `tests/`.
- `tests/` - pytest suite. `docs/` - protocol, numbered decision log, threat model, providers.

## Setup and running

```bash
uv sync                  # Python 3.12, editable install
uv run muvue --help
```

The dashboard and extension need Node 22 (`npm ci` in each directory).
`.claude/skills/verify/SKILL.md` has the end-to-end recipe for driving the CLI against a throwaway repo.

## Definition of done

A change is done when `scripts/check.sh` passes. It is the same command CI runs: `uv sync --locked`,
the full pytest suite with the 85% coverage gate on `muvue.core`, the hook latency benchmark, the
dashboard typecheck/tests/build (and a check that the committed `src/muvue/api/static/index.html`
matches the build), and the extension unit tests. The full run takes a few minutes.

The VS Code Extension Host acceptance test (`xvfb-run -a npm run test:host`) needs an X server and a
VS Code download, so it runs in CI only.

## Conventions

- Match the surrounding code; keep changes small and scoped to the task.
- Bug fixes come with a regression test.
- After changing `dashboard/`, run `npm run build` there and commit the rebuilt
  `src/muvue/api/static/index.html`; CI fails if it is stale.
- User-visible changes get an entry in `CHANGELOG.md` (same format as the existing entries: Added / Changed under a version).
  Design decisions go in the numbered log in `docs/decisions.md`; protocol changes in `docs/protocol.md`.
- `src/muvue/_hook.py` runs on every hook call under a latency budget: it imports only the stdlib
  and never `muvue.core` (see its module docstring).
- Commit messages: a short imperative summary line, then a body that explains why, in plain prose.
- `.coverage` is tracked, so running the checks rewrites it. Do not commit that change.
- Known flake: `tests/test_control_socket.py::test_request_link_rejects_non_json` has failed
  intermittently with "dropped the connection ([Errno 32] Broken pipe)" instead of "not JSON".
  Re-run before investigating; do not loosen the assertion.

## Prohibited

- No secrets, tokens or credentials in any file, test fixture or log.
- Never run muvue against this repository's own `.muvue/`; use a scratch repo.
- No deploys or releases (no publishing to PyPI or the VS Code marketplace).
- No pushes to `main`, force-pushes or history rewrites unless explicitly asked. Work on a branch.
