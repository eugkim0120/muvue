# muvue

[![ci](https://github.com/eugkim0120/muvue/actions/workflows/ci.yml/badge.svg)](https://github.com/eugkim0120/muvue/actions/workflows/ci.yml)

Tired of vibecoding, where an agent works through a plan you can only
find by scrolling back through a wall of chat? muvue is a CLI and a
live dashboard for working with AI coding agents through a plan you
can see and approve, instead of a chat transcript.

You and the agent agree on a spec and break it into tasks, each with
acceptance criteria. You approve both, then the agent does the work,
either in your own session or unattended through `muvue run`. The
dashboard shows the plan as a diagram of tasks and dependencies, colored
by status, and each task's diff, logs and notes. Anything that needs you
waits in one inbox.

- **You approve before the agent starts.** First the spec, then the
  task list. Approving the task list freezes each task's acceptance
  criteria, so the agent can't quietly change what "done" means.
- **muvue checks the work itself.** When the agent says a task is done,
  muvue runs your tests and linter and rates the change's risk. Small,
  clean changes are approved automatically. Changes that touch tests,
  risky paths or outdated parts of the project wait for your review.
- **Everything is on the record.** Every step is saved with who took
  it and how muvue knows: you at a terminal, you in the dashboard, a
  git hook, or an agent it detected. The agent's commits are linked to
  their task.
- **It can run on its own.** `muvue run` hands approved tasks to an
  agent CLI (`claude` and `opencode` are verified), with a spending
  limit per agent, rate-limit handling and an emergency stop.

A few words used below: muvue's commands call a task a **node** and
refer to it by number. The two approvals before work starts are
**gates**: `approve spec:N` for the spec, `approve gate2:N` for a
project's task list, and `approve review:N` for a finished task.

## Requirements

- Python 3.12 or newer, and `git`
- [`uv`](https://docs.astral.sh/uv/) (recommended), `pipx`, or `pip`
- Optional:
  - an agent CLI, if you want muvue to run agents unattended; `claude`
    and `opencode` are the ones verified end to end (see
    `docs/providers.md`);
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

When a task is marked done, muvue runs `[checks] test` and `lint` from
`.muvue/config.toml` (default `pytest -q` and `ruff check .`). Set them to
commands that work in your repository first. A failing check sends the
task to review instead of approving it. Likewise `worktree_setup`
(default `uv sync`) runs in every new task worktree in strict mode or
with `worktree_mode = "per_node"`; set it to your project's setup
command, or to `""`.

Then plan, approve, run and review:

```bash
muvue project create --goal "add a health endpoint"              # -> project 1
muvue spec 1 --title "Health endpoint" --body "GET /health returns 200"   # -> spec, node 1
muvue approve spec:1                                             # approve the spec
muvue decompose 1 --title "Implement /health" \
  --criteria "GET /health returns 200" --criteria-mode auto \
  --predicted-touches "app/*.py"                                 # -> a task, node 2
muvue approve gate2:1                                            # approve the task list; criteria freeze
muvue run                                                        # an agent works every ready task
muvue status                                                     # counts by status
muvue approve review:2                                           # if it stopped for review
muvue close 1                                                    # preview what goes into project memory
muvue close 1 --yes                                              # commit it and close the project
```

`init` sends every task to the `fake` agent, which reports success
without touching files, so this runs anywhere. `muvue uninit` removes
everything `init` added and leaves the repository as it was.

Some commands are meant for you, at a terminal or in the dashboard:
`approve`, `reject`, `answer`, `ack`, `merge`, `close`, `pause`,
`resume` and `handoff`. When one of them runs under an agent CLI, muvue records it as
the agent's action (`actor_evidence = agent_parent:<name>`) and counts it
in the dashboard's approval figures. The call still goes through: this
is detection, not prevention (see `docs/threat-model.md`).

## Running agents unattended

Add an agent to `.muvue/config.toml` and route tasks to it. This
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

`muvue run` gives each ready task's agent a fresh session, with the
task's brief on stdin. The agent is told to commit with a
`Muvue-Node: <id>` trailer and which checks muvue will run. After the
session, muvue links the commits, runs the checks and rates the risk.
Then it continues, or stops at the first task that needs you.

- `muvue run --node 7`: just that task. `--parallel 2` needs
  `worktree_mode = "per_node"`, so agents never share a checkout.
- `muvue pause 1`: emergency stop. The runner and its agent process are
  terminated, and tasks in progress go back to `ready`. `muvue resume 1`
  lets work continue.
- An agent at 100% of its budget gets no more tasks. A rate-limited
  one waits up to `max_wait_minutes` in the same run, then falls back or
  pauses (`on_rate_limit_timeout`).
- Agent output is kept in `.muvue/logs/<node>.log` (gitignored). Claude
  runs your own Claude Code hooks even in headless mode, so read these
  logs before sharing them.

For OpenCode (for example `deepseek-v4.1-flash` or
`mimo-v2.6-flash-free`), `docs/providers.md` has a working config.
It records exactly what was verified for each vendor, and what wasn't.

## Using it from your own agent session

```bash
muvue adapter install claude-code   # hooks in .claude/settings.json
muvue adapter install codex         # or gemini / cursor: instruction files
muvue mcp                           # MCP stdio server with the agent's commands only
```

The Claude Code hooks give the agent the current task's brief when a
session starts. They block edits while a task is waiting for your
approval, and keep a turn from ending while a task in progress has no
logged work or touches an outdated part of the project memory. Hooks
run through a stdlib-only fast path: on CI, p99 is 34 ms per call.

Agents use `muvue brief <node> [--budget N] [--since EVENT]`, `start`,
`note`, `ask --default ...`, `done` and
`fail --lesson --trigger --do-instead --scope`. While a task started
with `muvue start` is in progress, the `prepare-commit-msg` hook adds
its `Muvue-Node:` trailer to every commit, so commits link to the task
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
- **tree**: the plan as a diagram, with each task's parent and
  dependencies, colored by status.
- **task panel**: criteria, notes, commits, the real diff and a live log
  tail.
- **spec**: click a line to comment on it; the agent sees the comment in
  its brief.
- **inbox**: answer the agent's questions, approve reviews, and
  acknowledge warnings, audit drafts and project-memory updates.
- **timeline**, **revisions**, and **KPIs**: how far work drifted from
  the plan, how often risky work was approved within 10 seconds, and
  spend per agent.
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

With `mode = "strict"`, each task works in its own git worktree,
branched off a separate bare repository muvue calls the airlock. The
airlock accepts a push only to the branch of the task being worked on,
and never to `main`. `pre-push` in your checkout refuses commits that
name an unfinished task, and `muvue merge` brings finished branches onto
`main`.
This guards against accidents and lazy bypasses; it is not isolation
from a determined same-user process.

## Project memory

What a finished project leaves behind for the next one. `close` turns a project's decision notes, pinned lessons and the files
it actually changed into `decisions` and anchored `components`. It
commits them on `refs/heads/muvue/structure`, and fast-forwards `main`
only when your checkout is clean; otherwise it leaves an inbox item, or
opens a PR with `--pr`. A later commit that edits an anchored file
marks its component stale. The next task that touches a stale component
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
| `[risk]` | paths and diff size that raise a change's risk level |
| `[planning]` | task size limits, lease length, ask timeout, which risk levels need an `auto` criterion |
| `[agents.<name>]` | agent command, auth check, usage parser, cost model, budget, rate-limit policy |
| `[routing]` | which agent gets each kind of task |
| `[budget]` | wall-clock and task-count caps per `muvue run` |
| `[daemon]`, `[notify]` | bind address and extra allowed origins; a URL (ntfy topic or webhook) for inbox notifications |

## Development

```bash
uv sync
uv run pytest -q                        # about 855 tests, about 75 s
uv run pytest -q --cov=muvue.core       # enforces the 85% coverage gate (currently 90%)
cd vscode-extension && npm ci && npm test
MUVUE_CMD="uv run --project .. muvue" xvfb-run -a npm run test:host   # real VS Code, downloads it once
```

CI (`.github/workflows/ci.yml`) runs all of this on every push, plus the
hook latency benchmark against the v4 budgets.

## Known limitations

- **Dogfood gate: met on the last two projects, with caveats.** The
  plan's gate asks for 80% of state transitions to be logged without
  prompting, across two projects. On muvue's own projects 4 and 5, 18
  of 19 commits carry their task's trailer, and every task's status change
  was logged by the agent doing the work. The agent knew it was being
  measured, and it also approved the gates, which muvue recorded as the
  agent's (`agent_parent:claude`). Before the adapter was tightened,
  only 2 of 63 commits carried a trailer.
- **Codex and Gemini parsers are unverified.** Only the `claude` and
  `opencode` agents have been run end to end; see `docs/providers.md`.
- **Rate limits are tested on recorded output only.** No live run has
  hit one.

## Docs

- `docs/protocol.md`: task statuses, every command, API routes, events
  (protocol version 2)
- `docs/decisions.md`: numbered decision log
- `docs/threat-model.md`: the daemon's security model
- `docs/providers.md`: what's verified for each agent CLI
- `CHANGELOG.md`
