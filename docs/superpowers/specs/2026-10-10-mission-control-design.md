# Mission control: design

Date: 2026-10-10. Status: approved in conversation (2026-10-09 and
2026-10-10), screen by screen, on the Design canvas
https://claude.ai/artifact/TJ4nyFrsVfZ2F7PssygP51 (boards 15 to 21).

Builds on `2026-09-27-dashboard-redesign-design.md` and
`2026-09-27-project-canvas-design.md`. Both stay in force except where
this document replaces them. The replacements are listed under "What
this replaces".

## Goal

muvue is the place to watch and steer all agent work on a machine, not
only the plan of one project. A person opens it and sees, in this
order:

1. what needs them now, across every project;
2. every agent session, which project, task, worktree and branch it is
   in, what it is doing, and what it has spent;
3. for one project, a map of what the software does, with the agents
   placed on the parts they are changing.

The map exists before any code does. It is the plan drawn as the
software it will produce.

## Decisions taken in conversation

1. **Which agents.** muvue tracks the sessions it launches and the
   sessions a person starts by hand (for example a Claude Code session
   in a worktree). Sessions it launches can be started, stopped,
   retried and capped. Hand-started sessions are view-only: muvue does
   not send them messages or stop them.
2. **Sessions are first class.** A session does not have to belong to a
   task. Work with no task is shown as "off-plan", not flagged as an
   error.
3. **Scope.** One dashboard covers every repository where `muvue init`
   has run on this machine.
4. **Before a plan exists**, the map comes from the plan: decompose
   writes planned steps and modules, drawn dashed until code exists.
5. **Liveness** comes from the last hook event (thresholds below).
6. **One "Needs you" inbox**, ranked; phone push for questions,
   sessions waiting on the person, and failures.
7. **Controls** for launched sessions: start, stop, retry, budget cap.
8. **Home screen**: inbox, then sessions grouped by project.
9. **Collision warning** in the first version.
10. **Agent support**: Claude Code through hooks first; any other CLI
    registers through MCP tools.
11. **One map with zoom levels**: Steps, then Modules, then Files. A
    task's scenario is drawn on the map as an overlay, not as a separate
    view. No four-way view switch.
12. **Self-similar plan**: any node can open into its own map of child
    nodes, at any depth, with the same card shape.
13. **Executors**: each depth has a default agent; any node can
    override it.

## What this replaces

- Project canvas principle 1 ("no tabs"). The dashboard gets a Home
  screen and four project tabs: Map, Plan, Agents, Repo.
- Project canvas principle 4 is kept for the Map (steps and data, not
  code) and relaxed for the Agents and Repo tabs, which show branches,
  worktrees and files because that is their job.
- One daemon per repository (`cli/main.py:327`) becomes one daemon per
  machine (Phase 5).
- Nodes stop being limited to the depth spec, task, subtask (Phase 3).

What does not change: the single committed `index.html`, no network
except the daemon's API, no web fonts (the mockups use IBM Plex; the
product keeps the system font stack in `tokens.css`), the session and
auth model in `docs/threat-model.md` (Phase 5 updates it), `routes.ts`
as the only module that spells a path, 44 px touch targets, the contrast
and type-scale guard tests, and the existing gates.

## Vocabulary

- **Session**: one run of an agent CLI, from start to end. It has an
  origin (`launched` by muvue's runner, or `hand` for anything else),
  a working directory, and optionally a node.
- **Worktree**: a git worktree of a registered repository. muvue's own
  are under `~/.muvue/worktrees/<repo-hash>/node-<id>` on branch
  `node-<id>` (`core/strict.py:56-57`, `:130-210`). Hand-made ones can
  be anywhere.
- **Step**: something the software does, in the order a person meets
  it ("Record a take", "Detect pitch"). Steps hand data to each other.
- **Module**: an existing `components` row: a part of the code with file
  anchors.
- **Scenario**: an ordered list of hops through steps that a node must
  make work ("record one take": You, Browser, Server, Core, Browser).
- **Executor**: the configured agent (`[agents.<name>]`) that runs a node.

## Phases

Each phase ships on its own, with its own implementation plan, tests
and dashboard slice. Order:

| Phase | What | Depends on |
|---|---|---|
| 1 | Sessions and worktrees in one repository; Agents tab | - |
| 2 | Needs-you inbox and collisions | 1 |
| 3 | Nesting at any depth and per-node executors; Plan tab | - |
| 4 | Steps and scenarios; Map zoom levels and scenario overlay | 3 |
| 5 | One daemon for all repositories; Home screen | 1, 2 |

Phase 5 is last because it changes the security boundary and is easier
to get right once the per-repository pieces exist.

---

## Phase 1: sessions and worktrees

### Capturing sessions from hooks

Today the hooks read the Claude Code payload but keep only `tool_name`,
`node_id`, `stop_hook_active` and `summary` (`_hook.py:114-125`). They
find the node from `.muvue/current_node`, one file per repository
(`core/adapters.py:29`), which is wrong as soon as two sessions run in
one repository.

Changes:

1. Every hook queue line gains `session_id`, `cwd` and `transcript_path`
   from the payload when present, plus `agent` (`"claude"` for the
   Claude Code adapter).
2. New hook events installed by the Claude Code adapter:
   `UserPromptSubmit` (session is working again) and `SessionEnd`.
3. `PreToolUse` appends one queue line per call (today it appends
   nothing except on timeout, `_hook.py:225-252`). For `Edit` and
   `Write` the line carries `tool_input.file_path`. The hot-path budget
   is unchanged: an `O_APPEND` write, no SQLite, no subprocess.
4. A session's node is decided at drain time, not in the hook:
   - if `cwd` is inside a node's `worktree` (`nodes.worktree`), that
     node;
   - else, if the session was launched by the runner, the runner's node;
   - else none (off-plan).
   `.muvue/current_node` stays for the decision hooks' allow/deny logic
   and is not used to bind sessions.
5. **Finding the repository from a worktree.** `_find_repo_root` walks up
   to `.muvue/` (`_hook.py:82-91`). A worktree outside the repository has
   no `.muvue/`. When the walk finds a `.git` *file* (a linked worktree),
   the hook reads its `gitdir: <repo>/.git/worktrees/<name>` line and
   uses `<repo>` instead. This is a string parse, no subprocess.
6. **User-level install.** `muvue adapter install claude --user` writes
   the same hook commands into `~/.claude/settings.json`, so every
   Claude Code session on the machine reports, including sessions in
   worktrees whose checkout has no `.claude/settings.json`. A hook whose
   working directory is not inside a registered repository exits 0 at
   once. The repository-level install stays the default; `doctor`
   reports which one is active.

### Sessions table (schema version 9)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,            -- the agent's own session id; runner-made: "run-<uuid>"
    project_id INTEGER REFERENCES projects(id),
    node_id INTEGER REFERENCES nodes(id),   -- NULL = off-plan
    agent TEXT NOT NULL,            -- "claude", or an [agents.*] name
    origin TEXT NOT NULL CHECK (origin IN ('launched', 'hand')),
    cwd TEXT NOT NULL,
    branch TEXT,
    transcript_path TEXT,
    pid INTEGER,                    -- launched sessions only
    started_at TEXT NOT NULL,
    last_event_at TEXT NOT NULL,
    last_action TEXT,               -- "Edit web/tracks.ts", "turn ended", ...
    ended_at TEXT,
    waiting INTEGER NOT NULL DEFAULT 0,  -- 1 after Stop, 0 after UserPromptSubmit/PreToolUse
    alias_of TEXT REFERENCES sessions(id)  -- agent session id folded into a launched row
);
CREATE TABLE IF NOT EXISTS session_touches (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    path TEXT NOT NULL,             -- repository-relative
    at TEXT NOT NULL,
    PRIMARY KEY (session_id, path)
);
```

`drain_queue` (`core/hooks.py:274-342`) upserts `sessions` and
`session_touches` in the same transaction that writes the `hook.*`
events. Spend comes from the existing `node_usage` and `agent_spend`
for launched sessions; hand sessions show no spend (their cost is not
reported to muvue).

### Liveness

Computed when read, from `last_event_at`, `waiting` and `ended_at`:

| State | Rule |
|---|---|
| working | event in the last `working_s` (default 120 s) and not waiting |
| waiting on you | `waiting = 1` and not ended |
| idle | no event for more than `idle_s` (default 900 s) |
| ended | `ended_at` set, or no event for more than `ended_s` (default 7200 s) |

Thresholds live in a new `[sessions]` config table.

### Launched sessions

`runner.run_node` (`core/runner.py:445`) inserts a `sessions` row with
origin `launched`, the driver's pid, `cwd` = the worktree, before
invoking the driver, and sets `ended_at` when the driver returns. The
runner passes `MUVUE_SESSION=run-<uuid>` in the driver's environment.
Hooks fired by that agent inherit it and copy it into each queue line as
`launched_session`; the drain then records the agent's own `session_id`
as an alias of the launched row (`sessions.alias_of`), so one agent run
shows as one session.

### Other agent CLIs

New MCP tools in `mcp_server.py`: `session_start(agent, cwd, node_id?)`
and `session_heartbeat(session_id, action?, touched_paths?)` and
`session_end(session_id)`. They write the same queue lines the hooks
write. No vendor-specific adapters in this phase.

### Worktrees

`core/gitutil.py` gains `list_worktrees(repo_root)`, which parses
`git worktree list --porcelain` into path, branch, head and flags, and
`branch_status(repo_root, branch)`, which returns ahead and behind
counts against the default branch and the changed paths
(`git diff --name-only <base>...<branch>`). Results are cached per
repository and per HEAD sha so a dashboard refresh does not spawn git
for unchanged worktrees.

A worktree with no live session is listed as "no agent", with its last
commit time and whether its branch is merged.

### API

- `GET /sessions?project_id&include_ended=0` returns sessions with
  computed state, node, worktree, branch, last action and spend.
- `GET /worktrees` returns every worktree of the repository with branch,
  ahead/behind, changed paths, linked session ids, linked node and stage.
- `POST /sessions/{id}/stop` stops a **launched** session (SIGTERM to its
  process group, as `drivers.stop_all` does). For a hand session it
  returns 409 with "started by hand; stop it where it runs".

All under the existing auth, JSON-only POST and Origin rules.

**Stage** of a worktree: `worktree` (no commits ahead), `commits`,
`checks` (node checks running), `review` (node in review), `merged`.

### Dashboard: Agents tab (board 19)

- Tabs appear: Map, Plan, Agents, Repo. Map is today's canvas; Plan and
  Repo are placeholders until Phases 3 and 4 fill them.
- Branches section: a git graph down the left (main across the top, one
  row per branch, rows ordered by fork point so no line crosses
  another), then columns for branch and worktree, agent and state, task
  or "off-plan", changes, ahead/behind, and a five-segment stage bar.
- "Worktrees with no agent" below it.
- Side panel for the selected branch: session, planned files for its
  node with their state, overlap (empty until Phase 2), and Open
  session / Open diff / Stop agent (Stop only for launched sessions).

---

## Phase 2: needs-you inbox and collisions

### Collisions

A collision is two live work items touching the same repository path.
A work item is a live session (paths from `session_touches`) or an
unmerged worktree branch (paths from `branch_status`). Two items of the
same node do not collide.

- **Overlap** (both sides actual paths): shown in red on both items,
  in the inbox, and on the Map.
- **Planned overlap** (an actual path matches another node's
  `predicted_touches` glob, using `risk.touches_globs`,
  `core/risk.py:79`): shown only in the node's side panel.

`core/collisions.py` computes them on read; nothing is stored. Merge
conflicts at merge time (`core/merge.py`) are unchanged.

### Inbox

`GET /inbox` (`api/app.py:435`) gains three item kinds and a fixed
order:

1. agent questions (exists);
2. sessions waiting on you (new, from `sessions.waiting`);
3. gates to approve (exists);
4. reviews (exists);
5. collisions (new);
6. failures and budget caps (exists).

Within a kind, newest first. Each item carries project, node, session
and age, so the Home screen in Phase 5 can merge inboxes.

Phone push through `core/notify.py` gains `session.waiting` (sent once
per waiting spell, after it has lasted 60 s) and `collision.found`.

---

## Phase 3: nesting at any depth and per-node executors

### Depth

The `kind` CHECK stays (`spec`, `task`, `subtask`); depth comes from
`parent_id`. A `subtask` may now have `subtask` children.

- `revisions.add_child` (`core/revisions.py:104-160`) accepts a subtask
  parent: before Gate 2 approval of the ancestor task it makes a
  `subtask`; after, it goes through `replan_add_subtask`, which accepts
  task **or** subtask parents (today task only, `:52`).
- `POST /nodes/{id}/breakdown` and `muvue _breakdown` accept subtasks
  (today spec or task, `api/app.py:904`, `cli/main.py:993`).
- New setting `planning.max_depth` (default 4: spec 0, task 1, subtask
  2 and 3). Breakdown and add-child refuse deeper nodes with a clear
  error.
- **Roll-up**, display only, computed in `GET /graph`: each node gets
  `children_total`, `children_done`, and `done_when` (all children done
  and the node's own criteria pass). The state machine is unchanged: a
  parent is still approved through its own review.
- The runner already schedules only `task` and `subtask`
  (`core/runner.py:219`). A node with children is not scheduled itself
  until its children are done.

### Executors

- New column `nodes.agent TEXT` (NULL = use the default), schema
  version 10.
- Default per depth: `routing.spec` at depth 0, `routing.task` at depth 1,
  `routing.subtask` at depth 2 and deeper.
- `runner.agent_for_node` (`core/runner.py:188-197`) order becomes:
  CLI `--agent` override, then `nodes.agent`, then the `runner:<agent>`
  owner, then the depth default.
- Breakdown uses the same rule for the node being broken down (today
  spec or task routing only, `api/app.py:912`).
- `POST /nodes/{id}/edit` accepts `agent` (a configured agent name, or
  null to clear). It is a human verb.
- `config._routing_agents_exist` and `agent_status.ROLES` stay as they
  are; per-node agents are validated on write.

Cheaper models and the Codex shim are configuration, not code:
`[agents.codex] command = "codex exec --json"`, `usage_parser =
"codex_json"`; `[agents.cheap] command = "opencode run --format json
--model <provider/model>"`, `usage_parser = "opencode_json"`. The README
gains these examples.

### Dashboard: Plan tab (boards 20, 21)

- A table of nodes by wave: id, title, state, needs, builds steps (empty
  until Phase 4), agent, checks. Critical path and Edit plan / + Task in
  the header.
- Selecting a node opens its side panel: needs, unblocks, builds,
  done-when criteria (frozen at Gate 2), Runs on (executor with "set on
  this node" or "layer default", Change, Use layer default), and Inside
  (children, or Break down).
- Opening a node with children shows its own map: the same card shape
  (id, state, title, runs on, "N inside"), dependency arrows, a
  breadcrumb (project / wave / node) and Up one level, and a done-when
  bar.

---

## Phase 4: steps, scenarios and the map's zoom levels

### Data (schema version 11)

```sql
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL          -- reading order
);
CREATE TABLE IF NOT EXISTS step_modules (
    step_id INTEGER NOT NULL REFERENCES steps(id),
    component_id INTEGER NOT NULL REFERENCES components(id),
    PRIMARY KEY (step_id, component_id)
);
CREATE TABLE IF NOT EXISTS step_edges (
    src INTEGER NOT NULL REFERENCES steps(id),
    dst INTEGER NOT NULL REFERENCES steps(id),
    data TEXT NOT NULL,                -- "samples"
    data_type TEXT,                    -- "Float32Array", shown at module zoom
    PRIMARY KEY (src, dst, data)
);
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_hops (
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id),
    seq INTEGER NOT NULL,
    lane TEXT NOT NULL,                -- "You", "Browser", "Server", "Core"
    text TEXT NOT NULL,
    step_id INTEGER REFERENCES steps(id),
    component_id INTEGER REFERENCES components(id),
    PRIMARY KEY (scenario_id, seq)
);
```

`components.status` gains `planned`. The CHECK cannot be altered in
SQLite, so the migration rebuilds `components` (create new, copy, drop,
rename) inside one transaction. `component_edges` (today unused) holds
module-to-module data edges with `kind` = the data label.

### Where they come from

- Breakdown of a spec (`parse_breakdown`, `core/drivers.py:110`) accepts
  optional `steps`, `step_edges`, `modules` (name, purpose, file globs,
  step) and, per child, `scenario` and `modules` lists. Missing keys are
  allowed: the map then falls back to today's canvas.
- Steps, modules and scenarios are part of the plan: they change under
  the same Gate 2 and revision rules as nodes.
- A module turns from `planned` to `current` when a merged commit
  touches one of its anchors (the existing drift and close code paths,
  `core/drift.py`, `core/close.py`).

### Roll-up chain

file path, then module (by `components.anchors_json` globs), then step
(by `step_modules`). Sessions, worktrees and collisions are placed on
the map through this chain. A step's state is the most active of its
modules' states (review, then building, then built, then planned).

### Dashboard: Map tab (boards 16, 17)

- Zoom: Steps, Modules, Files.
  - **Steps**: step boxes in reading order, wrapping like text, with the
    modules listed under each title and the task numbers; arrows carry
    the data name; outside endpoints (Microphone, Downloads) as icon and
    name with no box.
  - **Modules**: today's component map with data edges, data types on
    the arrows.
  - **Files**: each module's files from anchors and actual touches,
    grouped by module. No import graph in this phase (muvue has no
    import parser; `core/imports.py` imports GitHub issues).
- Selecting a node: its steps get a clay ring, its scenario hops get
  numbered badges, agents at work get tags under the steps they touch,
  everything else fades to half opacity. The map header shows this key
  while a node is selected.
- A strip under the map header explains the four node states (planned,
  building, in review, built) with the selected node's state
  highlighted.
- Side panel: "How #N shows on the map" (steps and files, scenario,
  agents with worktree and branch) and the scenario's numbered hops with
  lane names.
- The Map is drawn by `canvas/` (hand-written layout,
  `canvas/flowLayout.ts`). Step layout is a fixed grid of up to four
  columns per row with a return connector between rows; no layout
  library is added.

---

## Phase 5: one daemon for all repositories, and Home

### Registry

`~/.muvue/repos.json`: `{"repos": [{"path": ..., "added_at": ...}]}`,
mode 0600. `muvue init` adds the repository, `muvue uninit` removes it,
and `muvue register <path>` adds one initialised before this existed.
A listed path that is gone is shown as "missing" with a Remove action,
never dropped silently. Tests run with an isolated `HOME` so they never
touch the real registry.

### One process

- `muvue serve` (no path) serves every registered repository. `muvue
  serve <path>` keeps working and serves that repository only.
- `create_app` (`api/app.py:61-76`) is split: the security middleware,
  session manager and auth routes move to an outer app; each repository
  gets a router built by today's route code and mounted at
  `/r/<repo-id>/`, where `<repo-id>` is the first 16 hex characters of
  the repository hash (`core/strict.py:44-45`).
- Daemon settings (`bind`, `port`, `tailnet_logins`) move from each
  repository's `config.toml` to `~/.muvue/config.toml`. A repository
  config that still sets them makes `doctor` warn; the daemon ignores
  it.
- The lifespan loop drains every repository's queue.
- New home endpoints: `GET /home/repos`, `GET /home/sessions`,
  `GET /home/inbox`, each merging the per-repository answers.

### Security

`docs/threat-model.md` is updated in the same change. One session
cookie now authorises every registered repository, so the blast radius
of a stolen cookie or an allowlisted tailnet device grows from one
repository to all of them. Controls 1 to 7 are otherwise unchanged and
`doctor`'s live probes cover the outer app. A decision record explains
why one daemon was chosen over a home page that queries per-repository
daemons (one place to secure and probe; one systemd unit for Spec C).

### Dashboard: Home (board 18)

- Top bar: muvue, "All projects", count of agents working and waiting,
  sign-in state.
- NEXT bar: the top inbox item and its primary action.
- Left: Needs you, ranked, most urgent first.
- Right: agents and worktrees grouped by project: state, agent and
  origin, task or off-plan, worktree and branch, doing, last active,
  spend; collisions inline in red; Open map per project.

---

## Error handling

- A hook never fails the agent: any error writes a `hook_error` queue
  line and exits 0, as today.
- A queue line for a session whose repository cannot be found is
  dropped by the hook (it is outside muvue), not queued.
- `git` failures in `list_worktrees` or `branch_status` are shown on the
  affected row ("git failed: <first line of stderr>"), never hidden and
  never fatal to the page.
- Stopping a hand session is refused with a reason, not ignored.
- A breakdown that exceeds `planning.max_depth`, or names an unknown
  agent, is refused with the limit or the name in the message.

## Testing

- Every phase: failing test first for each behaviour; `uv run pytest -q`
  stays green; coverage stays at or above 85%.
- Phase 1: hook payload capture (golden queue lines), drain to sessions
  (state transitions working, waiting, idle, ended), worktree parsing
  against real `git worktree` fixtures, the `.git` file walk, the
  `--user` install round-trip with `uninit`.
- Phase 2: collision matrix (session/session, session/branch,
  branch/branch, same node), inbox ordering.
- Phase 3: add-child and breakdown at depth 2 and 3, the depth limit,
  `agent_for_node` order, the edit verb's validation.
- Phase 4: the components rebuild migration on a version-9 database with
  data, breakdown parsing with and without steps, roll-up chain.
- Phase 5: registry round-trip, two repositories served by one app,
  auth applies to both mounts, `doctor` probes.
- Dashboard: vitest for each new view; the existing guard tests
  (contrast, type scale, touch targets, no stray fetch,
  `test_dashboard_static.py` route parity); `scripts/ui-check.mjs` at
  390 and 1280 wide for each new tab.

## Out of scope

- Sending messages to, redirecting, or stopping hand-started sessions.
- Spend for hand-started sessions.
- A function-level zoom and an import graph (needs a TypeScript import
  parser).
- Vendor adapters other than Claude Code's hooks (other CLIs use the MCP
  tools).
- Several machines in one dashboard.
- The systemd unit (Spec C). Phase 5 lands after it, and Spec C's unit
  then becomes one unit per machine.
