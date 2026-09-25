# muvue

muvue runs an AI coding agent's work as an auditable state machine instead of
a chat transcript: every task is a node in a SQLite-backed graph
(`pending -> ready -> in_progress -> review -> done`), every mutation is a
recorded event, and a two-gate approval flow (spec approval, then task
decomposition approval) sits between an agent's plan and it touching your
repo. An unattended runner can then drive approved work through pluggable
vendor-CLI drivers (`claude`, `codex`, `gemini`, or `fake` for testing),
enforcing per-driver budgets, risk-tiered auto-approval, and — in strict
mode — a worktree-per-node git airlock that blocks a `push` unless the
node's frozen acceptance criteria are met.

## Requirements

- Python >= 3.12
- [`uv`](https://docs.astral.sh/uv/)
- `git`
- `gh` CLI, authenticated, only if you use `muvue import --from github#N` or
  `muvue merge --pr --create`

## Install

No clone needed — pick one:

```bash
pip install muvue          # from PyPI
pipx install muvue         # isolated, puts `muvue` on PATH
uvx muvue --help           # try it without installing anything
uv tool install muvue      # isolated, uv-managed
```

Installing gives you two console scripts: `muvue` (the CLI) and
`muvue-fake-agent` (a scripted driver for testing, no vendor subscription
required).

### From source

```bash
git clone https://github.com/eugkim0120/muvue && cd muvue
uv sync
```

Run via `uv run muvue ...`, or `uv pip install -e .` into an active venv to
get `muvue` directly on `PATH`. Also installable straight from GitHub without
cloning: `pipx install git+https://github.com/eugkim0120/muvue`.

## Quickstart

muvue operates on an external git repo — point it at one:

```bash
uv run muvue init /path/to/your/repo
uv run muvue doctor /path/to/your/repo
```

`init` scaffolds `.muvue/` in that repo: `config.toml`, `muvue.db`, git hook
shims, and a `.gitignore` entry. `doctor` validates the install and, if no
daemon is already running, spins up a throwaway one against a scratch repo
to live-probe its security controls (loopback bind, Host/Origin checks) then
tears it down.

Walk a project through both gates and have the (fake, no-login-required)
driver execute it:

```bash
uv run muvue project create --goal "add a health endpoint" --path /path/to/your/repo
# -> prints the new project's id

uv run muvue spec <project_id> --title "Health endpoint" \
  --body "Add GET /health returning 200" --path /path/to/your/repo
# -> Gate 1: spec node, status=pending

uv run muvue approve spec:<spec_id> --path /path/to/your/repo
# -> spec node, status=ready

uv run muvue decompose <spec_id> --title "Implement /health" \
  --criteria "GET /health returns 200" --path /path/to/your/repo
# -> Gate 2: task node, status=pending

uv run muvue approve gate2:<project_id> --path /path/to/your/repo
# -> freezes acceptance criteria, unblocks the task

uv run muvue run --path /path/to/your/repo
# -> unattended runner drives ready nodes through the routed driver
#    (config.toml routes every kind to `fake` by default)

uv run muvue approve review:<node_id> --path /path/to/your/repo
uv run muvue close <project_id> --yes --path /path/to/your/repo
# -> commits the project's proposed structure diff, flips it to closed
```

`uv run muvue uninit /path/to/your/repo` reverses everything `init` did —
config, db, hook shims, gitignore entry, and the `muvue/structure` git ref
if present — leaving the repo byte-for-byte as it was before.

## Other entry points

- `uv run muvue serve --path <repo>` — one daemon per repo: HTTP API +
  SSE-driven dashboard at `http://127.0.0.1:8765` (loopback only).
- `uv run muvue mcp --path <repo>` — MCP stdio server exposing agent verbs
  (`start`/`done`/`fail`/`ask`/...), for wiring muvue into an editor or
  agent harness directly instead of driving it from the CLI.
- `uv run muvue audit --path <repo>` — structure-graph drift audit: samples
  stale/unverified components, drafts proposed diffs into the inbox, and
  archives lessons that have decayed past their retrieval threshold.
- `uv run muvue import --from github#<N> --path <repo>` / `muvue merge --pr
  --create` — real `gh` CLI integration; needs `gh auth login` first.

## Configuration

`.muvue/config.toml`, written by `init` with working defaults. Key sections:
`mode` (`light` or `strict`), `[agents.<name>]` (driver command + budget +
cost unit), `[routing]` (node kind -> driver name), `[risk]` (globs and
diff-size thresholds that raise risk tier), `[budget]` (wall-clock and
node-count caps for `muvue run`).

## Development

```bash
uv run pytest        # 537 tests
```

No network or vendor-CLI login is required for the test suite or the
quickstart above — everything routes through the `fake` driver unless you
edit `[routing]` yourself.

## Known limitations

- The `claude`/`codex`/`gemini` driver output parsers were built from
  documented/synthetic samples of each vendor CLI's output format, not
  verified against a live, subscription-authenticated run of that CLI.
- The VS Code extension (`vscode-extension/`) compiles and unit-tests
  clean but has not been run inside a real VS Code Extension Host.

## Docs

- `docs/protocol.md` — node status machine, verb reference, event schema.
- `docs/decisions.md` — numbered decision log, append-only.
- `docs/threat-model.md` — daemon/API security model.
- `docs/providers.md` — vendor driver adapter contracts.
- `CHANGELOG.md`
