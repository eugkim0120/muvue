# Project canvas: design

Date: 2026-09-27. Status: approved in conversation, section by section.
Builds on `2026-09-27-dashboard-redesign-design.md` (the Preact app,
toolchain, colour tokens and session model), which stays in force except
where this document replaces it.

## Goal

The dashboard becomes the primary way a person works with a muvue
project and its agents. They should not need the CLI for anything they
do day to day.

Opening a project shows one thing first: a diagram of what is being
built. The spec sits at the root. Tasks are boxes laid out in the order
data flows through them, with each arrow labelled by what it carries.
Subtasks are nested inside their task. Every box says which agent owns
it and what that agent is doing now. Anything waiting on the person
arrives as a notification card they can approve, reject or discuss in
place. When the project has no spec or no tasks yet, the diagram is
where they create them, by typing or by asking an agent to break the
work down.

The main user is on a phone over Tailscale. Desktop at 900 px and wider
is an equal target, not a fallback.

## Principles

1. **The canvas is the page.** There are no Plan, Inbox, Activity or
   Spend tabs, and no separate spec page. Everything about a project is
   reached from its canvas.
2. **Human-verb parity.** Every CLI verb a person uses has a place on the
   page: `project create`, `spec`, `decompose`, `approve`, `reject`,
   `answer`, `ack`, `run`, `merge`, `close`, `pause`, `resume`, `handoff`,
   `propose-revision` and node comments. Setup and admin verbs stay
   CLI-only: `init`, `uninit`, `doctor`, `migrate`, `rebuild`, `export`,
   `adapter install`, `mcp`, `audit`, `hook`, `serve`.
3. **Nothing happens invisibly.** Every request the page starts shows a
   pending state exactly where its result will land, until the result
   arrives or fails. Every agent's current work is visible on the box it
   is working on.
4. **Logical and data flow, not code.** The canvas shows purpose, order
   and what passes between steps. File paths, diffs and logs stay in the
   node sheet.
5. **Only what matters now.** Show counts and states that need a
   decision. Hide the rest behind a tap.

## What does not change

- Single self-contained `index.html` built from `dashboard/`, committed,
  CI checks it is fresh. No CDN, no web fonts, no network except the
  daemon's API.
- Session model, `routes.ts` as the only module that spells a path,
  `fetch(` only in `client.ts`/`auth.ts`, no web storage, no inline
  handlers, no `alert`/`confirm`/`prompt`, 44 px touch targets, the
  Claude palette in light and dark.
- Mutating requests are JSON and may carry `X-Request-Id`.
- The existing gates. Page-created tasks and agent breakdowns still need
  Gate 2 ("approve task list"). After Gate 2, changes go through the
  existing replan/revision rules.
- The node sheet keeps its actions directly under its title (the phone
  reachability fix).

## Screen layout

### Web (900 px and wider)

```
┌────────────┬──────────────────────────────────────┬──────────────┐
│ Projects   │ Voxscore: voice to sheet music  ▾    │ Needs you  3 │
│ ● Voxscore │ planning · 3/9 done · 2 agents busy  │ ┌──────────┐ │
│   Tuner    │ · $1.20 of $10          [▶ Run] [⌘K] │ │ card     │ │
│            ├──────────────────────────────────────┤ └──────────┘ │
│ + New      │                                      │ ┌──────────┐ │
│            │        canvas (pan / zoom / fit)     │ │ card     │ │
│            │                                      │ └──────────┘ │
└────────────┴──────────────────────────────────────┴──────────────┘
```

- **Left sidebar:** projects, each with a dot that is filled when it has
  cards waiting. "+ New project" at the bottom.
- **Header:** goal as the title, with the project menu (▾) holding
  Pause/Resume, Merge finished, History, Agents and Close. Under it is
  one status line: phase, tasks done out of total, how many agents are
  busy, and spend against budget. Spend turns amber past 80 % of the
  budget and red at the cap. Tapping the status line opens the Agents
  panel. **▶ Run** launches agents on every ready task (`muvue run`).
- **Canvas:** the diagram (below).
- **Right rail:** notification cards. It collapses to a thin strip with
  a count when empty.
- **Node sheet:** slides in from the right over the rail.
- **Keyboard:** ⌘K opens the palette. Arrow keys move focus between
  boxes and Enter opens one. With a card focused, A approves and R starts
  a reject. Esc closes the topmost layer only.

### Phone (below 900 px)

- The top bar holds the project name (tap it to switch projects or
  create one), the status line, and a ⋯ button for the project menu.
- The cards come first, as a stack. Once the user scrolls past them, the
  stack collapses to a sticky "3 need you" pill that scrolls back to
  them. Swiping a card right approves it, and swiping left opens the
  reject field. The buttons are always present as well, so swiping is
  never the only way.
- The canvas becomes a vertical flow: tasks run top to bottom in flow
  order, with a labelled arrow between them. Tasks that run in parallel
  sit side by side if they fit at 390 px, and stack otherwise, under a
  "parallel" bracket. Subtasks are collapsible rows inside their task.
- The node sheet is a bottom sheet with its actions at the top.
- A floating **▶ Run** button sits bottom-right, clear of the Safari
  toolbar via `env(safe-area-inset-bottom)`.

## The canvas

### Structure

```
┌ Spec: Voxscore — voice in, sheet music out ────── ● approved ┐
│                                                               │
│ ┌ Record voice ───┐ audio   ┌ Detect pitch ───┐ notes   ┌ Export ──┐
│ │ capture mic     │ frames  │ turn audio into │ ──────► │ MusicXML │
│ │ ✓ mic access    │───────► │ ● YIN tracker   │         │ ○ writer │
│ │ ✓ ring buffer   │         │ ○ smoothing     │         │ ○ valid. │
│ │ claude · done   │         │ codex · 3m ◉    │         │ queued   │
│ └─────────────────┘         └─────────────────┘         └──────────┘
│   [+ task]  [✨ Break down with agent]                        │
└───────────────────────────────────────────────────────────────┘
```

- **Spec root card:** spans the canvas top. It holds the title, the
  first line of the spec as a subtitle, a status pill and a badge when a
  card is waiting on it. Tapping it opens the spec's sheet, which holds
  the full text, line comments and actions.
- **Task box:** title, purpose line (first non-empty line of `body_md`,
  one line, ellipsised), subtask rows (status glyph + title, up to 4 then
  "+N more"), agent chip, and a status-coloured left bar. The box grows
  to fit its rows. Tap opens the node sheet; tapping a subtask row opens
  the subtask's sheet.
- **Flow arrow:** drawn from one task to a task that depends on it,
  using the `deps` edge. If the edge has a `carries` label, the label
  sits at the arrow's midpoint in a small pill. An edge without a label
  draws as a thinner "then" arrow with no label. Arrows are orthogonal
  (right-angle routed), not curves.
- **Parent edges are not drawn.** Hierarchy is shown by nesting: tasks
  inside the spec frame, subtasks inside tasks.
- **No code detail:** paths, criteria text, diffs and logs never appear
  on the canvas.

### Layout (web)

- Column = longest path to the task over `deps` edges (as `layout.ts`
  does today for layers), laid out left to right. Tasks with no deps sit
  in column 0.
- Within a column, tasks are ordered by the mean row of their
  predecessors, then by id.
- Box width 240 px. Box height depends on the number of subtask rows.
  Column gap 72 px, leaving room for arrow labels. Row gap 24 px.
- A cycle (a data bug) does not hang the layout: the existing `seen`
  guard stays.
- Pan by dragging the background or with two fingers, and zoom with
  pinch, ctrl+wheel or the +/− buttons. "Fit" resets the view. The page
  itself never scrolls sideways (`scrollWidth` equals viewport width);
  only the canvas viewport pans.

### Layout (phone)

- Same column order, flattened top to bottom. Tasks in the same column
  form one "step". If two fit side by side at the viewport width minus
  gutters, they do. Otherwise they stack under a bracket labelled
  "parallel".
- Between steps, one arrow carries the union of the incoming labels,
  deduplicated.

### States on the canvas

- **Status colours:** the existing `--st-*` tokens on the left bar and
  the subtask glyphs (✓ done, ● in progress/review, ○ pending/ready, ⚠
  blocked/failed).
- **Needs-you badge:** a small accent dot on any box or subtask row that
  has an open card. Tapping the dot scrolls or pans to its card and
  highlights it.
- **Ghost boxes:** while a breakdown runs on a node, three pulsing
  placeholder boxes sit where its children will appear. They carry the
  caption "claude is breaking this down…" and a "view log" link. Real
  boxes replace the ghosts as they arrive over SSE.
- **Pending creates:** a task added by hand appears at once as a ghost
  box with the typed title. It becomes real when the POST returns, or
  turns into an inline error with Retry.
- **Skeleton:** on first load, a grey spec bar with three grey boxes.
  The page never shows the text "loading…".
- **Focus ring:** keyboard focus draws a 2 px accent ring on the box.

## Creating from the page

| State | What the canvas shows |
|---|---|
| No projects | A centred "Start a project" card: a goal field and a Create button. |
| Project, no spec | An empty spec root card holding the inline form: title, body (multi-line, one line per requirement) and "Submit spec". |
| Spec pending | The spec root with an "Approve spec" card in the rail. `decompose` needs an approved spec, so "+ task" and "✨ Break down" are shown but disabled, captioned "Approve the spec first". |
| Spec approved, no tasks | Under the spec: "+ task" and "✨ Break down with agent". |
| Tasks exist | "+ task" at the end of the flow, "+ subtask" inside each task box (shown on hover on web, always on phone), and "✨ Break down" on each task, which breaks that task into subtasks. |

**Add task/subtask form** (inline, in place of the + button):

- Title (required).
- Purpose, one line, written to the first line of `body_md`.
- Acceptance criteria, one per line; optional before Gate 2.
- "Receives from": a picker of existing tasks, each with an optional
  "carrying ___" text. It writes `deps` edges with `carries`.
- Save and Cancel.

**Remove:** before Gate 2, a task box's menu has "Remove". It soft-deletes
the node and its subtasks, and asks for confirmation in a sheet.

**After Gate 2:** "+ subtask" goes through `replan_add_subtask`: inside
the parent's predicted touches and `max_subtasks`, or gated. "+ task"
opens "Propose plan revision" (`propose-revision`) instead of creating
a task directly, and the result arrives as a card to approve.

## Agent breakdown

- `POST /nodes/{id}/breakdown {agent?}` on a spec (produces tasks) or a
  task (produces subtasks).
- The agent is the `agent` given, else `[routing].spec` for a spec node,
  else `[routing].task` for a task node. The `spec` routing entry
  already exists for this purpose (`runner.py:207-213`).
- Breakdown does not run through `core/runner.py`'s ready-node loop
  (spec nodes are excluded there on purpose, and its loop only handles
  `task`/`subtask` work, not decomposition). Instead the daemon spawns a
  new internal CLI verb (`muvue _breakdown --node ID --agent X`) as a
  detached subprocess, the same shape as `start?agent`'s `_spawn_runner`
  helper: its own process, its own log file, killable by `pause`. It
  records `breakdown.started {node_id, agent, pid}`. When the
  process exits, it records `breakdown.finished {node_id, created: [ids]}`
  or `breakdown.failed {node_id, reason}`. Output goes to
  `.muvue/logs/breakdown-<node>.log`.
- **Prompt:** the spec or task text, the existing children, and an
  instruction to create each child with `muvue decompose` (or `replan`
  for subtasks). Each child gets a title, a one-line purpose,
  acceptance criteria and `--depends-on ID --carries TEXT` for its
  inputs. The agent must not write code.
- Only one breakdown per node at a time. A second request returns 409
  and the canvas keeps the running one's ghosts.
- `pause` stops running breakdowns, like it stops runner processes.
- A finished breakdown on a spec produces an "Approve task list" card
  (Gate 2). On a task after Gate 2, the replan rules decide whether its
  subtasks need a card.
- The fake agent gains a breakdown behaviour: it creates 3 tasks with
  labelled deps. The tests and the live check use it.

## Agent transparency

- **Agent chip on every box:** agent name, then state.
  - `queued`: ready, no lease yet.
  - `running 3m`: live pulse, elapsed time since the lease started.
  - `waiting on you`: open question or review.
  - `done`, `failed`, or `paused` when the project is paused.
  - "unassigned" when no routing applies.
- **The chip names the agent that will run the node** (owner, else
  routing) before any run. The node sheet's "Why this agent" says which
  rule picked it: `owner`, `[routing].task`, or a handoff event.
- **Agents panel** (a sheet from the status line or the project menu):
  one row per configured agent, with:
  - its routing roles (spec/task/subtask);
  - its current work: a node link and elapsed time, or "idle";
  - runs today;
  - spend against its budget;
  - its last error, if any.

  It answers "who is doing what" at a glance.
- **Runs section** in the node sheet: every attempt and breakdown on the
  node. Each row shows the agent, start time, duration, outcome, cost,
  and a log tail that expands to the full log.
- **History** (project menu): the project-wide event timeline from
  today's Activity page, as a sheet. It is not a tab.

## Notification cards

One card per open item:

| Kind | Primary actions | Discuss does |
|---|---|---|
| Spec awaiting approval | Approve · Reject | Comment thread on the spec node |
| Task list awaiting Gate 2 | Approve · Reject | Comment on the spec |
| Task in review | Approve · Reject (reason) | Comment on the node, diff link |
| Agent question | Answer (inline field) | Same thread |
| Blocked/failed node | Retry · Hand off | Comment on the node, log link |
| Plan revision proposed | Approve · Reject | Comment on the revision |
| Structure update / audit / unattributed commit / unverified external | Acknowledge | Comment where a node exists |

- A card shows the kind as a caption, the node title, one line of
  context (what changed, the question text, the block reason), the
  agent that raised it, and how long ago.
- Reject opens an inline reason field. "Send back" is disabled while the
  field is empty.
- Discuss expands the card to show the node's comments (existing
  `/nodes/{id}/comment` and notes), with a reply field. For a question,
  the answer box is the reply field.
- Tapping the card's title pans the canvas to the node and highlights
  it for 1.5 s.
- After an action, the card shows a spinner in the pressed button, then
  collapses out. On error it stays and shows the message inline.
- Cards come from the existing `/inbox` data, with no new data source.
  Each item maps to its node where it has one.

## Node sheet

Order, top to bottom:

1. Title, status pill and agent chip.
2. **Actions**: the ones valid for this node's state (approve, reject,
   start with agent, retry, hand off, merge, break down, remove).
3. Purpose and acceptance criteria. Editable before Gate 2; after it,
   an edit starts a revision.
4. Flow: "receives X from A", "sends Y to B", as tappable links.
5. Subtasks (for a task).
6. Runs.
7. Discussion (comments and notes, with a reply field).
8. A "Details" disclosure holding predicted and actual touches, diff and
   logs. Code detail lives only here.

The spec node's sheet shows the full spec text with line-anchored
comments, the behaviour of today's spec page, in place of item 3.

## API changes

All new mutating endpoints require a session, JSON bodies and
`X-Request-Id` dedupe inside `write_txn`. Each logs a replayable event.

| Endpoint | Does | Core path |
|---|---|---|
| `POST /projects {goal}` | create a project | `projects.create_project` (no project-level budget field exists; budgets are per-agent in `config.toml`, unchanged) |
| `POST /projects/{id}/spec {title, body_md}` | submit spec | same as `muvue spec` |
| `POST /nodes/{id}/children {title, body_md, criteria[], depends_on: [{id, carries?}]}` | task under spec / subtask under task | `create_node` before Gate 2; `replan_add_subtask` after |
| `POST /nodes/{id}/remove` | soft-delete before Gate 2 | new `nodes.remove_node` (sets `deleted_at` on node + subtasks, event `node.removed`) |
| `POST /nodes/{id}/edit {title?, body_md?, criteria?}` | edit before Gate 2 | existing criteria edit path; after Gate 2 → 409 with hint to propose a revision |
| `POST /nodes/{id}/breakdown {agent?}` | launch breakdown agent | new `breakdown` module |
| `POST /projects/{id}/run {parallel?}` | launch `muvue run` for the project | tracked subprocess, like `start?agent` |
| `GET /agents/status` | per-agent roles, current work, spend | reads leases, events, `agent_spend`, config |
| `GET /nodes/{id}/runs` | attempts and breakdowns for the node | reads events + logs |

`GET /agents` stays. `GET /graph` gains `carries` on dep edges, and the
`owner`/routing-resolved `agent` on each node.

**Schema:** `deps` gains a nullable column, `carries TEXT`. This bumps
`SCHEMA_VERSION` from 7 to 8 and adds a `migrate` step. No `dep.added`
event exists today (`deps` rows are written silently inside
`create_node`/`replan_add_subtask`), and `rebuild` does not replay
`deps` at all currently — this is a pre-existing gap, not something
already working. This plan adds a `dep.added` event (carrying
`carries`) at both write sites, and adds `deps` replay to `rebuild`, so
the schema's history becomes reconstructible the way every other table
already is. The CLI gains
`--carries` alongside `--depends-on` on `decompose` and `replan`. The
MCP tool gets the same field.

`protocol_version` is bumped, and `docs/protocol.md` lists the new
routes and the flag. `docs/decisions.md` gets these entries:

- the canvas replaces tabs;
- human-verb parity;
- breakdown uses `[routing].spec`;
- `carries` on `deps`.

`docs/threat-model.md` notes that breakdown and project run spawn
processes from the API, under the same controls as `start?agent`.

## Frontend structure

Replaces `plan/`, `inbox/`, `activity/`, `spend/`, `spec/` directories.

- `canvas/`:
  - `flowLayout.ts`: pure; columns, rows, box sizes, arrow routes and
    label points.
  - `Canvas.tsx`: pan/zoom viewport, SVG arrow layer, HTML boxes.
  - `SpecRoot.tsx`, `TaskBox.tsx`, `SubtaskRow.tsx`, `GhostBox.tsx`,
    `AgentChip.tsx`, `AddForm.tsx`.
  - `PhoneFlow.tsx`: the vertical rendering, which shares `flowLayout`
    columns.
- `cards/`:
  - `cardsFromInbox.ts`: pure; maps `/inbox` to typed cards linked to
    nodes.
  - `CardRail.tsx`, `Card.tsx`, `Discuss.tsx`.
- `project/`:
  - `ProjectPage.tsx`: header, status line, canvas and rail.
  - `StatusLine.tsx`, `AgentsSheet.tsx`, `HistorySheet.tsx` (reuses
    Timeline), `NewProject.tsx`, and the existing `CloseSheet.tsx`.
- `node/`: extended with `Runs.tsx`, `Flow.tsx`, `Discussion.tsx`,
  `Details.tsx`, and the spec text with line comments (moved from
  `spec/`).
- `pending.ts`: a small registry of in-flight requests keyed by where
  their result will appear. The canvas and cards read it to draw ghosts
  and spinners.
- **Router:** `#/p/:projectId` with `?node=<id>`, plus `#/` (project list
  or new project). The old hashes (`#/plan`, `#/inbox`, `#/activity`,
  `#/spend`, `#/spec/:id`) redirect to the current project's canvas, and
  `#/spec/:id` opens that node's sheet.
- `useApi(path, deps)` hook: a fetch with an alive-guard, loading and
  error state, used by every view. This retires the per-page copies the
  final review flagged.
- SSE refreshes are debounced by 200 ms.

## Error handling

- A failed read shows an inline error with Retry in the region that
  failed: the canvas, the rail or a sheet section. The rest of the page
  stays usable.
- A failed action keeps its ghost or card and shows the daemon's message
  and a Retry button inline. A toast appears only when there is no
  place to put the message.
- `403` shows the existing token banner. `409` shows the daemon's
  message as the inline error, for example "a breakdown is already
  running on this task" or "plan is frozen; propose a revision".
- A breakdown that fails turns its ghosts into one error box that shows
  the reason, a log link and Retry.

## Testing

**Vitest:**

- `flowLayout`: columns from deps, ordering, box heights from subtask
  counts, arrow routes avoiding boxes, label points, cycle safety, and
  phone step grouping with parallel stacks.
- `cardsFromInbox`: each inbox kind becomes the right card, linked to
  the right node.
- Card actions: approve, reject reason required, discuss posts a
  comment, answer posts an answer, and the spinner shows until resolve.
- Ghosts: a pending create shows a ghost with the typed title, and a
  running breakdown shows 3 ghosts that are replaced as SSE data
  arrives.
- Agent chip states, one assertion per state.
- Router redirects from the old hashes.
- The existing global-constraint tests keep passing:
  - no stray `fetch`;
  - no path literals outside `routes.ts`;
  - no storage;
  - 44 px targets;
  - node sheet actions sit above the details.

**Pytest:**

- Every new endpoint: auth required, JSON required, request-id dedupe,
  and gate rules (`/children` before and after Gate 2, `/remove` and
  `/edit` refused after Gate 2).
- Breakdown lifecycle with the fake agent: started, children created
  with `carries`, finished. A second breakdown gets 409, and pause stops
  it.
- `POST /projects/{id}/run` spawns and is tracked.
- Migrate 7 to 8, and `rebuild` reproduces `carries`.
- The `test_every_route_in_routes_ts_is_served` check covers the new
  routes.

**Live (Playwright, headless, phone 390×664 and desktop 1280×800, light
and dark)** on a scratch repo:

1. Create a project from the page.
2. Write and submit the spec, then approve its card.
3. Add a task by hand, with a "receives from" link.
4. Run a fake-agent breakdown and watch the ghosts turn into boxes.
5. Approve the task list card.
6. Press ▶ Run and watch the chips go `queued` to `running` to
   `waiting on you`.
7. Approve a review card by swipe on phone and by button on desktop.
8. Check that `scrollWidth` equals the viewport width.
9. Screenshot each step.

## Out of scope

- Editing `config.toml` (agents, routing, budgets) from the page. The
  Agents panel is read-only and says which file to edit.
- Setup and admin verbs (see Principles).
- Drag-to-reorder tasks or drawing flow arrows by dragging. Arrows come
  from the add/edit form's "receives from" and from breakdowns.
- Multi-user presence or permissions.
- Showing code structure (files, modules) on the canvas.
