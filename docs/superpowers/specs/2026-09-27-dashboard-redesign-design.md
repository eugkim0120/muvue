# Dashboard redesign: design

Date: 2026-09-27. Status: approved in conversation (approach C, Claude colours, full app-shell rethink).

## Goal

Replace the hand-written single-file dashboard (`src/muvue/api/static/index.html`, 802 lines, dark-only, six tabs, one modal) with an app built in Preact and TypeScript that is organised around the user's workflow, reads like a native Apple app on iPhone and Mac, uses the Claude colour palette in light and dark mode, and stays a single self-contained HTML file at runtime.

The main user is on a phone over Tailscale. Desktop and the VS Code webview must keep working.

## What does not change

- The daemon serves the dashboard at `GET /` by reading `STATIC_DIR / "index.html"` on every request. No new static routes.
- The page loads nothing from the network except the daemon's own API. No CDN, no web fonts, no `<script src>`.
- Session model: `#n=<nonce>` in the fragment is exchanged once for an HttpOnly cookie; inside the VS Code iframe the exchange also returns a token that lives in memory only. `localStorage`, `sessionStorage`, `indexedDB` and `document.cookie` never appear in the built file.
- No inline event handler attributes in markup.
- Every mutating request is `Content-Type: application/json` and may carry `X-Request-Id`.
- The VS Code extension keeps opening `GET /#n=<nonce>` in an iframe.
- The API. One read-only endpoint is added: `GET /agents` returns `{"agents": ["claude", "fake", ...]}`, the names under `[agents.*]` in `.muvue/config.toml`, sorted. It exists so "start with agent" can be a picker instead of a text prompt.

## Toolchain and packaging

- Source in `dashboard/` beside `vscode-extension/`: Preact 10, `@preact/signals`, TypeScript, Vite, `vite-plugin-singlefile`, Vitest with `@testing-library/preact` and jsdom.
- `npm run build` writes `dashboard/dist/index.html` with all JS and CSS inlined, then copies it to `src/muvue/api/static/index.html`. `npm run dev` is `vite build --watch` with the same copy, so the running daemon serves each rebuild.
- `build.minify` is `false`: the committed artifact stays readable and diffable.
- The built `index.html` is committed. Python users never need node.
- CI job `dashboard`: `npm ci`, `npm run typecheck`, `npm test`, `npm run build`, then `git diff --exit-code -- src/muvue/api/static/index.html`. A stale artifact fails CI.
- Runtime deps: `preact`, `@preact/signals`. Everything else is a dev dependency.
- `tests/test_dashboard_static.py` keeps the four security checks against the built file. The endpoint check now reads the paths from `dashboard/src/api/routes.ts`, the one module allowed to spell an API path; a Vitest test asserts `fetch(` is called only in `dashboard/src/api/client.ts`.
- Decision #166 records this: a build step for the dashboard is accepted because the runtime contract (single file, no network fetches beyond the daemon) is unchanged and enforced by tests and CI.

## Visual language

Font stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, "Segoe UI", Roboto, sans-serif`; monospace `ui-monospace, "SF Mono", Menlo, monospace`. On Apple devices this is San Francisco without downloading anything.

Type scale: 12 (caption), 13 (secondary), 15 (body), 17 (title), 22 (page title), 28 (KPI). Titles weight 600. Line height 1.4.

Radii: 10 (controls), 14 (cards, sheets). Shadows: light `0 1px 2px rgb(0 0 0 / .06), 0 4px 12px rgb(0 0 0 / .06)`; dark `0 1px 2px rgb(0 0 0 / .4)`. Motion: 180 ms ease-out for sheets and segmented controls; none under `prefers-reduced-motion`.

Touch targets at least 44 px tall on phone. Bottom tab bar and bottom sheet pad by `env(safe-area-inset-bottom)`.

### Colour tokens (`:root`, switched by `prefers-color-scheme`)

| token | light | dark |
|---|---|---|
| `--bg` | `#FAF9F5` | `#1F1E1D` |
| `--surface` | `#FFFFFF` | `#2B2A27` |
| `--surface-2` | `#F0EEE6` | `#353431` |
| `--border` | `#E5E2D9` | `#3E3D39` |
| `--text` | `#1F1E1D` | `#F4F3EE` |
| `--text-2` | `#6B6A66` | `#A8A69F` |
| `--accent` | `#D97757` | `#D97757` |
| `--accent-hover` | `#C4623F` | `#E58A6C` |
| `--on-accent` | `#FFFFFF` | `#1F1E1D` |
| `--danger` | `#C94F4F` | `#E06B6B` |

Status colours, same in both modes, tinted backgrounds via `color-mix(in srgb, var(--status) 14%, var(--surface))`:

| status | colour |
|---|---|
| pending | `#8A8986` |
| ready | `#5B8DEF` |
| in_progress | `#D9A33B` |
| review | `#8C6FD6` |
| awaiting_approval | `#C46FA8` |
| done | `#4C9A6A` |
| blocked | `#C94F4F` |
| failed | `#9C2F2F` |

Risk tier text: low `--text-2`, medium `#D9A33B`, high `--danger`.

## Information architecture

Four destinations. Hash routes so the page keeps working from `GET /` alone and inside the iframe; the nonce is stripped from the hash before the router starts.

| route | page |
|---|---|
| `#/plan` | Plan: spec card, gates, diagram or list of tasks |
| `#/spec/:id` | Spec reader with line comments |
| `#/inbox` | Everything waiting on the user |
| `#/activity` and `#/activity/revisions` | Timeline; plan revisions |
| `#/spend` | KPIs and spend per agent |

`?node=<id>` on any route opens the task sheet over the page. Closing it removes the query. Back button closes it.

### Shell

Desktop (viewport at least 900 px): left sidebar 240 px with the project switcher at top (goal, phase pill, kebab with pause / resume / close), then the four destinations (inbox shows a count badge), then at the bottom the session state ("signed in" / "read-only") and protocol version. Content area max width 1100 px.

Phone (under 900 px): top bar with project name and phase pill (tap opens the project switcher as a bottom sheet with the same actions), content, bottom tab bar with Plan / Inbox / Activity / Spend, SF-style icons drawn as inline SVG, inbox badge.

Signed-out state: a banner at the top of the content with a token field ("api token printed by `muvue serve`") and a "use token" button. All action buttons are hidden while read-only, not disabled, so a read-only viewer sees a clean page.

Errors and confirmations: toasts bottom-centre (desktop) or above the tab bar (phone), 4 s, dismissable. Destructive actions (pause, close project, reject) confirm in a sheet, never `alert()` / `confirm()` / `prompt()`.

Live updates: the SSE stream bumps a refresh signal; every page re-fetches on it. A refresh never closes an open sheet; the sheet re-fetches its own node instead.

### Plan page

Top: the spec card. Title, status pill, first lines of the body, "read spec" link to `#/spec/:id`. If the spec is `pending` and the user is signed in, a filled accent button "Approve spec". If the spec is approved, tasks exist and project phase is `planning`, a filled accent button "Approve task list" (`POST /nodes/{project_id}/approve {"target":"gate2"}`), with the line "Approving freezes each task's acceptance criteria." If the project has no spec, an empty state: "No spec yet. Create one with `muvue spec <project>`."

Below: a segmented control Diagram / List. Default: Diagram on desktop, List on phone.

Diagram: the existing layered layout algorithm ported to `dashboard/src/plan/layout.ts` unchanged in behaviour (layer = longest path over parent and dep edges; siblings ordered by mean predecessor position). Nodes are 200 x 52 rounded cards on `--surface` with a 4 px left bar and a status dot in the status colour, title in `--text`, second line "kind · status · risk" in `--text-2`. Parent edges solid `--border`, dep edges dashed `--accent`. The SVG is inside a scroll container with pinch-zoom left to the browser. Tap opens the sheet. Nodes are keyboard focusable.

List: tasks grouped by status in workflow order (review, awaiting_approval, blocked, in_progress, ready, pending, done, failed). Each row: status dot, "#id title", right-aligned risk tier, chevron. Tap opens the sheet.

### Task sheet

Right side sheet 440 px on desktop, bottom sheet on phone at `calc(100dvh - env(safe-area-inset-top) - 24px)` with a grab handle. Escape, the overlay, the close button and the back button all close it.

Header: "#id title", status pill, risk tier, then a caption "kind · owner · parent #n". Block reason shown as a callout when present.

Actions row directly under the header, before any long content (this is what fixed the phone bug in a524cad and stays):

| condition | buttons |
|---|---|
| status `review` | Approve (filled), Reject (danger outline; opens an inline textarea "what should change?" plus Send) |
| status `awaiting_approval` | Approve changed criteria |
| kind `spec` and status `pending` | Approve spec |
| status `ready` | Start with agent (opens a picker fed by `GET /agents`; on success toast "started runner pid N") |

Then a segmented control Overview / Diff / Logs.

- Overview: body (rendered as preformatted text), criteria as a checklist-style list with a caption "mode, verification" in words (`checked by muvue` / `unverified` / `human`), summary, predicted touches, notes as cards (kind, pinned, time), commits as short shas.
- Diff: source caption (patches of linked commits / worktree against branch point / nothing committed yet, plus "truncated" when set), then the coloured diff in a horizontally scrollable monospace block.
- Logs: last 200 lines, monospace, auto-scrolled to the end, Refresh button, re-fetched on every SSE tick while this segment is visible.

### Spec page

`#/spec/:id`. Title, status pill, and the same Approve spec button as the Plan card when applicable. Body rendered line by line with line numbers. Tapping a line opens a comment composer under it (input plus Comment button). Existing `[Ln]` feedback notes render as callouts under their line with an accent left border; general comments at the bottom with a textarea composer. Comments are `POST /nodes/{id}/comment {"text", "line"}`. When the project has several specs, the Plan card links to each and this page shows one.

### Inbox

Sections in this order, each hidden when empty; an empty inbox shows "Nothing waiting on you." with a checkmark illustration drawn as inline SVG.

1. Questions: text, node link, default answer and "safe to default" note, input plus Answer.
2. Awaiting review: node rows with risk tier; tap opens the sheet where Approve / Reject live.
3. Unverified external criteria: node rows with the caption "muvue could not run these checks; verify before approving".
4. Criteria changed, awaiting approval: node rows.
5. Blocked: node rows with the block reason.
6. Structure updates: message, PR link or PR error, Ack.
7. Audit drafts: component name and message, collapsible diff, Ack.
8. Commits touching components without a task: sha and files, Ack.
9. Unattributed commits: sha, files, Ack.

Badge count is the sum of all nine lists, as today.

### Activity

Segmented Timeline / Revisions.

Timeline: newest 100 events for the current project, grouped by day with a sticky day header, each row "type" in `--text`, "actor (evidence) · time" in `--text-2`, node link when present. Filter chips above: All, Approvals, Agent, Commits, Other (matched on event type prefix: `approve`, `agent`/`start`/`done`/`fail`, `commit`; everything else Other). The range slider is dropped.

Revisions: one card per plan revision: "Revision n", approved time or "pending", the diff rendered as a two-column key/value list (JSON fallback for nested values), Approve revision button when pending.

### Spend

KPI cards in a responsive grid (min 160 px): components verified in the last 50 commits, touches outside the prediction, rubber-stamp rate with its "n fast of m timed approvals" caption, tokens per task, worst agent spend vs budget. Then "Spend per agent": a card per agent with a progress bar (accent, amber past `warn`, danger when `exhausted`) and "spent / limit unit".

### Command palette

`⌘K` / `Ctrl+K` on desktop, a search button in the phone top bar. A sheet with one input. Results: tasks matching by number or title (from `GET /nodes?project_id=`), projects ("switch to #n goal"), and actions (Pause, Resume, Close project, Approve spec, Approve task list) that are valid for the current state. Enter runs the first result. Arrow keys move.

### Close project

From the project menu. A sheet with the close preview: "not closeable yet: tasks #a, #b are not done" when applicable; then decisions, promoted lessons, components and changed components as lists with counts; a checkbox "open a GitHub PR if main can't be fast-forwarded"; a danger button "Close and commit structure", disabled until closeable; result reported as a toast.

## Architecture

```
dashboard/
  package.json  vite.config.ts  tsconfig.json  index.html
  src/
    main.tsx                 boot: exchange nonce, then mount <App/>
    app.tsx                  shell: sidebar / tab bar, router outlet, sheets, toasts
    api/routes.ts            every API path, one function per route
    api/client.ts            fetch wrapper: JSON, token header, 403 -> signedOut
    api/auth.ts              nonce exchange, checkAuth, token entry
    api/stream.ts            EventSource -> refreshTick signal
    state.ts                 signals: projects, projectId, authed, inboxCount, refreshTick, toasts
    router.ts                hash parsing, navigate, useRoute
    ui/                      Button, Pill, Card, Segmented, Sheet, Toast, Confirm, Empty, Icon
    shell/                   Sidebar, TabBar, TopBar, ProjectMenu, TokenBanner, CommandPalette
    plan/                    PlanPage, SpecCard, Diagram, layout.ts, TaskList
    node/                    NodeSheet, Actions, Overview, Diff (+ diff.ts classifier), Logs, StartPicker
    spec/                    SpecPage
    inbox/                   InboxPage
    activity/                ActivityPage, Timeline, Revisions
    spend/                   SpendPage
    project/                 CloseSheet
    styles/tokens.css        colour, type, radius, shadow tokens, both schemes
    styles/base.css          reset, layout primitives, utilities
  test/                      vitest: setup.ts plus one file per module above
```

Data flow: pages call `api()` in `useEffect` keyed on `[projectId, refreshTick]`. Mutations call `post()`, then `refresh()` (bumps `refreshTick`) and toast. The sheet reads `?node=` from the route and fetches `GET /nodes/{id}` itself.

Error handling: `api()` throws `ApiError(message, status)` from `detail` or `statusText`. A 403 on a POST flips `authed` to false and shows the token banner. Pages render the error message in place of content; mutations toast it. Nothing is swallowed.

## Testing

Vitest, jsdom:

- `layout.test.ts`: dependency sits above dependent; siblings keep order; a cycle does not hang.
- `diff.test.ts`: line classifier (meta, hunk, add, del).
- `router.test.ts`: `#/plan?node=7` parses to page `plan` and node 7; navigate updates the hash; nonce fragment is never a route.
- `client.test.ts`: JSON content-type on POST, bearer header when a token is set, 403 on POST calls the signed-out hook, `detail` becomes the error message.
- `NodeSheet.test.tsx`: for each status, the expected action buttons render, and they render before the Overview segment in DOM order; Reject shows the textarea and posts `{feedback}`.
- `SpecCard.test.tsx`: Approve spec for a pending spec, Approve task list when phase is `planning` with tasks, neither when read-only.
- `InboxPage.test.tsx`: empty state; badge count equals the sum of lists.
- `no-stray-fetch.test.ts`: `fetch(` appears only in `src/api/client.ts` and `src/api/auth.ts`; every path literal starting with `/` lives in `src/api/routes.ts`.

Python (`tests/test_dashboard_static.py`): no external resources, no inline handlers, no web storage, every route in `routes.ts` is served by `create_app`. `tests/test_api.py`: `GET /agents` lists configured agent names and needs no session.

Manual verification before merge, with the local headless Chromium (see memory `local-headless-browser`): 390 x 664 and 1280 x 800 screenshots of Plan, task sheet with Approve spec visible without scrolling, Inbox, Activity, Spend, in light and dark (`page.emulateMedia({colorScheme})`). Approve a spec on a scratch repo through the sheet.

## Out of scope

Editing specs or criteria in the dashboard; creating projects or tasks; authentication changes; the VS Code extension beyond confirming it still loads the page.
