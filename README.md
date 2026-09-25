# muvue

[![ci](https://github.com/eugkim0120/muvue/actions/workflows/ci.yml/badge.svg)](https://github.com/eugkim0120/muvue/actions/workflows/ci.yml)

muvue runs an AI coding agent's work as an auditable state machine instead of
a chat transcript.

- **Every task is a node** in a SQLite-backed graph:
  `pending -> ready -> in_progress -> review -> done`, plus `blocked`,
  `awaiting_approval` and `failed`.
- **Every change is a recorded event**, with who made it and how muvue
  knows (a TTY, the dashboard's session, a hook, or a detected agent
  parent process).
- **Two gates stand between a plan and your repo**: a human approves the
  spec, then approves its decomposition into tasks, which freezes their
  acceptance criteria.
- **At `done`, muvue runs your checks itself** and assigns a risk tier.
  Low-risk work auto-approves. Anything touching tests, risky paths or a
  stale component goes to human review.
- **An unattended runner** drives approved work through vendor CLIs
  (`claude`, `codex`, `gemini`, or `fake` for testing), with per-driver
  budgets, rate-limit handling and an emergency stop.
- **A local dashboard** shows the plan as a DAG, each node's diff and
  logs, and an inbox of everything waiting on you.

## Requirements

- Python 3.12 or newer, and `git`
- [`uv`](https://docs.astral.sh/uv/) (recommended), `pipx`, or `pip`
- Optional:
  - an agent CLI, if you want muvue to run real agents; `claude` is the
    one verified end to end (see `docs/providers.md`);
  - an authenticated `gh`, for `close --pr`, `merge --pr --create` and
    `import --from github#N`.

## Install

```bash
uv tool install muvue      # or: pipx install muvue / pip install muvue
uvx muvue --help           # or try it without installing
```

This gives you two commands: `muvue`, and `muvue-fake-agent`, a scripted
stand-in agent that needs no subscription.

From source:

```bash
git clone https://github.com/eugkim0120/muvue && cd muvue
uv sync
uv run muvue --help
```

## Quickstart (five minutes, no agent login)

muvue works on an existing git repository. Every command takes `--path`
(default: the current directory).

```bash
cd /path/to/your/repo
muvue init          # writes .muvue/ (config, db), git hook shims, a .gitignore block
muvue doctor        # checks the install, and live-probes the daemon's security controls
```

At `done`, muvue runs `[checks] test` and `lint` from
`.muvue/config.toml` (default `pytest -q` and `ruff check .`). Set them to
commands that work in your repository first. A failing check sends the
node to review instead of approving it. Likewise `worktree_setup`
(default `uv sync`) runs in every new node worktree in strict mode or
with `worktree_mode = "per_node"`; set it to your project's setup
command, or to `""`.

Then plan, approve, run and review:

```bash
muvue project create --goal "add a health endpoint"              # -> project 1
muvue spec 1 --title "Health endpoint" --body "GET /health returns 200"   # -> spec node 1, pending
muvue approve spec:1                                             # Gate 1
muvue decompose 1 --title "Implement /health" \
  --criteria "GET /health returns 200" --criteria-mode auto \
  --predicted-touches "app/*.py"                                 # -> task node 2
muvue approve gate2:1                                            # Gate 2: criteria freeze
muvue run                                                        # the routed driver works every ready node
muvue status                                                     # counts by status
muvue approve review:2                                           # if it stopped for review
muvue close 1                                                    # preview what goes into the structure layer
muvue close 1 --yes                                              # commit it and close the project
```

`init` routes every node kind to the `fake` driver, which reports success
without touching files, so this runs anywhere. `muvue uninit` removes
everything `init` added and leaves the repository as it was.

Human verbs (`approve`, `reject`, `answer`, `ack`, `merge`, `close`,
`pause`, `resume`, `handoff`) are meant for a person at a terminal or the
dashboard. When one of them runs under an agent CLI, muvue records it as
the agent's action (`actor_evidence = agent_parent:<name>`) and counts it
in the dashboard's rubber-stamp figures. The call still goes through: this
is detection, not prevention (see `docs/threat-model.md`).

## Running real agents

Add a driver to `.muvue/config.toml` and route node kinds to it. This
config ran six tasks unattended against claude 2.1.281:

```toml
[agents.claude]
command = "claude -p --output-format stream-json --verbose --model sonnet --permission-mode acceptEdits --allowedTools 'Read Edit Write Glob Grep Bash(git add *) Bash(git commit *) Bash(git status*) Bash(git diff*) Bash(git log*) Bash(pytest*)'"
auth_check = "claude --version && claude auth status"
usage_parser = "claude_stream_json"
cost_model = "usd"
pinned_version = ">=2.1"

[agents.claude.budget]
unit = "usd"          # list price, even under a subscription
limit = 10.0

[routing]
task = "claude"
subtask = "claude"
```

`muvue run` gives each ready node's agent a fresh session, with the
node's brief on stdin. The agent is told to commit with a
`Muvue-Node: <id>` trailer and which checks muvue will run. After the
session, muvue links the commits, runs the checks and tiers the risk.
Then it continues, or stops at the first node that needs you.

- `muvue run --node 7`: just that node. `--parallel 2` needs
  `worktree_mode = "per_node"`, so agents never share a checkout.
- `muvue pause 1`: emergency stop. The runner and its agent process are
  terminated, and in-flight nodes go back to `ready`. `muvue resume 1`
  lets work continue.
- A driver at 100% of its budget is not scheduled again. A rate-limited
  one waits up to `max_wait_minutes` in the same run, then falls back or
  pauses (`on_rate_limit_timeout`).
- Driver output is kept in `.muvue/logs/<node>.log` (gitignored). Claude
  runs your own Claude Code hooks even in headless mode, so read these
  logs before sharing them.

`docs/providers.md` records exactly what was verified for each vendor,
and what wasn't.

## Working alongside an interactive agent

```bash
muvue adapter install claude-code   # hooks in .claude/settings.json
muvue adapter install codex         # or gemini / cursor: instruction files
muvue mcp                           # MCP stdio server with the agent verbs only
```

The Claude Code hooks inject the current node's brief at session start.
They block edits while a node is `awaiting_approval`, and keep a turn
from ending while an `in_progress` node has no logged work or touches a
stale component. Hooks run through a stdlib-only fast path: on CI, p99 is
34 ms per call.

Agents use `muvue brief <node> [--budget N] [--since EVENT]`, `start`,
`note`, `ask --default ...`, `done` and
`fail --lesson --trigger --do-instead --scope`. While a node started
with `muvue start` is `in_progress`, the `prepare-commit-msg` hook adds
its `Muvue-Node:` trailer to every commit, so commits link to the node
without the agent having to remember.

## Dashboard

```bash
muvue serve        # loopback only, port 8765
```

`serve` prints two lines:
- **`dashboard (one-time link, works once)`**: open it, and the page
  swaps the `#n=` nonce for an HttpOnly session cookie. Reopening the
  same link later gives a read-only view.
- **`api token: ...`**: for scripts and the VS Code extension (send it as
  `Authorization: Bearer <token>`). It exists only in the daemon's memory
  and changes on every restart.

The views:
- **tree**: the project as a DAG, with parent and dependency edges.
- **node panel**: criteria, notes, commits, the real diff and a live log
  tail.
- **spec**: click a line to comment on it; the agent sees the comment in
  its brief.
- **inbox**: answer questions, approve reviews, and ack signals, audit
  drafts and structure updates.
- **timeline**, **revisions**, **KPIs** (drift, touch drift, rubber-stamp
  rate, spend per driver).
- Pause, resume and close buttons, which act on the project chosen in
  the selector.

Every mutating request must be `Content-Type: application/json` with a
valid session, and foreign `Host` or `Origin` headers are refused;
`muvue doctor` probes all of this live. See `docs/threat-model.md`.

The **VS Code extension** (`vscode-extension/`) opens the same dashboard
in a webview, and maps `approve` and `pause` to commands. It asks for the
api token once per VS Code session and keeps it in memory. CI runs it in
a real VS Code Extension Host against a live daemon.

## Strict mode

With `mode = "strict"`, each node works in its own git worktree, branched
off a bare "airlock" repository. The airlock's `pre-receive` hook accepts
a push only to the branch of a node that is actively bound, and never to
`main`. `pre-push` in your checkout refuses commits that name an
unfinished node, and `muvue merge` brings finished branches onto `main`.
This guards against accidents and lazy bypasses; it is not isolation
from a determined same-user process.

## Structure layer

`close` turns a project's decision notes, pinned lessons and the files
it actually changed into `decisions` and anchored `components`. It
commits them on `refs/heads/muvue/structure`, and fast-forwards `main`
only when your checkout is clean; otherwise it leaves an inbox item, or
opens a PR with `--pr`. A later commit that edits an anchored file
marks its component stale. The next node that touches a stale component
goes to review, and approving it re-verifies the component. `muvue
audit` drafts re-verification diffs, and archives lessons nobody has
used in a while.

## Configuration

`.muvue/config.toml` is written by `init` and validated strictly: a typo
fails with the exact key.

| Key | What it does |
|---|---|
| `mode`, `worktree_mode`, `worktree_setup` | `light` or `strict`; `branch` or `per_node` worktrees; the command run in each new worktree |
| `[checks]` | `test` and `lint`, run by muvue at `done` |
| `[risk]` | globs and diff size that raise the risk tier |
| `[planning]` | task size limits, lease length, ask timeout, which tiers need an `auto` criterion |
| `[agents.<name>]` | driver command, auth check, usage parser, cost model, budget, rate-limit policy |
| `[routing]` | node kind to driver name |
| `[budget]` | wall-clock and node-count caps per `muvue run` |
| `[daemon]`, `[notify]` | bind address and extra allowed origins; a URL (ntfy topic or webhook) for inbox notifications |

## Development

```bash
uv sync
uv run pytest -q                        # about 840 tests, about 75 s
uv run pytest -q --cov=muvue.core       # enforces the 85% coverage gate (currently 90%)
cd vscode-extension && npm ci && npm test
MUVUE_CMD="uv run --project .. muvue" xvfb-run -a npm run test:host   # real VS Code, downloads it once
```

CI (`.github/workflows/ci.yml`) runs all of this on every push, plus the
hook latency benchmark against the v4 budgets.

## Known limitations

- **Dogfood gate: met on the last two projects, with caveats.** The
  plan's gate asks for 80% of state transitions to be logged without
  prompting, across two projects. On muvue's own projects 4 and 5, 10
  of 11 commits carry their node's trailer, and every node transition
  was logged by the agent doing the work. The agent knew it was being
  measured, and it also approved the gates, which muvue recorded as the
  agent's (`agent_parent:claude`). Before the adapter was tightened,
  only 2 of 63 commits carried a trailer.
- **Codex and Gemini parsers are unverified.** Only the `claude` driver
  has been run end to end; see `docs/providers.md`.
- **Rate limits are tested on recorded output only.** No live run has
  hit one.

## Docs

- `docs/protocol.md`: status machine, every verb, API routes, events
  (protocol version 2)
- `docs/decisions.md`: numbered decision log
- `docs/threat-model.md`: the daemon's security model
- `docs/providers.md`: what's verified per vendor CLI
- `CHANGELOG.md`
