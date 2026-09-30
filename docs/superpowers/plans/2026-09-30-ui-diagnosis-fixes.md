# UI Diagnosis Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the nine UI/UX flaws found in the dashboard review: write actions offered while signed out, a phone diagram pushed below the fold, an empty review card, fixed-height task boxes with bare subtasks, a diagram that does not scale to 40 tasks, no project-level progress or spend, flat hierarchy and type, weak contrast, and a handful of small issues.

**Architecture:** Every change is in the Preact dashboard (`dashboard/src`) plus its built single-file bundle at `src/muvue/api/static/index.html`. No backend change is needed: inbox `review` rows are `SELECT * FROM nodes` so `summary` and `risk_tier` are already in the JSON, `GET /nodes/{id}/diff` already returns the patch text (the `+N -M` line is counted client-side), and `GET /kpis` (an open route) already returns `spend_by_driver` with `{agent, unit, spent, limit, pct}`. Box heights stay layout-computed (so two boxes can never overlap) but are now estimated from content instead of fixed; ui-check verifies the estimate in a real browser.

**Tech Stack:** Preact + `@preact/signals`, Vitest + Testing Library, Vite single-file build, headless-Chromium ui-check.

**Spec:** The nine diagnosed flaws, restated per task below. Decisions to preserve: `docs/decisions.md` #173 (session/idle; only `X-Muvue-Background: 1` reads avoid extending a session) and #174 (the Next-step bar carries the one filled primary action; "Needs you" cards carry their own approve/acknowledge actions).

## Global Constraints

- No new runtime dependencies. Dashboard `dependencies` stay `@preact/signals` and `preact` only.
- Fail loudly: no fallbacks, no swallowed errors. A diff that cannot be fetched shows an explicit "diff unavailable" line, never a silent zero.
- Tests first. Run from `dashboard/`: `npx vitest run` (baseline 255 tests, must only grow), `npx tsc --noEmit`, `npm run build`.
- Every task that changes `dashboard/src` ends with `npm run build` (rewrites `src/muvue/api/static/index.html`) and commits that file with the source.
- ui-check must stay green (baseline 31/31) and is extended wherever a task claims a layout result. Run from the repo root:
  `PLAYWRIGHT_CORE=/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core CHROME_PATH=$HOME/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell node dashboard/scripts/ui-check.mjs`
- Backend tests stay green: `uv run pytest -q` (baseline 977 passed) once at the end of Task 8.
- Touch targets stay at least 44px (`test/touch-targets.test.ts`). Colour pairs are guarded by `test/contrast.test.ts`.
- Never write the session token to disk or logs. Do not touch the voxscore daemon on port 8766 or consume its nonce. Do not push, merge or publish.
- Commit messages are plain prose and end with these trailers:
  ```
  Co-Authored-By: Claude Code <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
  ```
- All work is on branch `feat/ui-diagnosis-fixes` in `/home/eugene/projects/muvue/.worktrees/ui-diagnosis-fixes`. Paths below are relative to that root.

## File Structure

- Modify: `dashboard/src/cards/{Card.tsx,CardRail.tsx,cardsFromInbox.ts,cards.css}`, `dashboard/src/node/diff.ts`, `dashboard/src/state.ts`
- Modify: `dashboard/src/canvas/{Canvas.tsx,TaskBox.tsx,SubtaskRow.tsx,flowLayout.ts,canvasData.ts,canvas.css}`; create `dashboard/src/canvas/pan.ts`
- Modify: `dashboard/src/project/{ProjectPage.tsx,FakeAgentNotice.tsx}`; create `dashboard/src/project/{ProgressBar.tsx,spend.ts}`
- Modify: `dashboard/src/styles/{base.css,tokens.css}`, `dashboard/src/ui/ui.css`, `dashboard/src/shell/{Sidebar.tsx,TopBar.tsx,shell.css}`
- Modify: `dashboard/scripts/ui-check.mjs`, `docs/decisions.md`, `CHANGELOG.md`
- Tests live in `dashboard/test/`; new ones: `diffStat.test.ts`, `pan.test.ts`, `collapseDone.test.ts`, `ProgressBar.test.tsx`, `spend.test.ts`, `type-scale.test.ts`, `CardRail.test.tsx`.

---

### Task 1: Signed-out view offers no write actions (flaw 1)

**Files:**
- Modify: `dashboard/src/cards/Card.tsx`, `dashboard/src/cards/CardRail.tsx`, `dashboard/src/project/ProjectPage.tsx`
- Test: `dashboard/test/Card.test.tsx`, create `dashboard/test/CardRail.test.tsx`, `dashboard/test/ProjectPage.test.tsx`

**Interfaces:**
- Produces: `Card` prop `readOnly?: boolean` (default false). `CardRail` prop `authed: boolean`. When not authed the rail renders the cards with `readOnly` and ONE button "Sign in to act" (`data-sign-in-to-act`) that sets `signInOpen.value = true`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/test/Card.test.tsx`:

```tsx
test("a read-only card shows no Approve, Reject or Discuss, but the title still opens the node", () => {
  render(<Card readOnly card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  expect(screen.queryByText("Approve")).toBeNull();
  expect(screen.queryByText("Reject")).toBeNull();
  expect(screen.queryByText("Discuss")).toBeNull();
  expect(screen.getByText("Detect pitch")).toBeEnabled();
});
```

Create `dashboard/test/CardRail.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { CardRail } from "../src/cards/CardRail";
import { signInOpen } from "../src/state";
import type { Card } from "../src/cards/cardsFromInbox";

const cards: Card[] = [
  { id: "task_review:5", kind: "task_review", nodeId: 5, projectId: 1, title: "Detect pitch", context: "in review", agent: "claude", ts: "" },
  { id: "task_review:6", kind: "task_review", nodeId: 6, projectId: 1, title: "Split voices", context: "in review", agent: "claude", ts: "" },
];

test("signed out: no write buttons, exactly one sign-in affordance", () => {
  signInOpen.value = false;
  render(<CardRail cards={cards} authed={false} />);
  expect(screen.queryByText("Approve")).toBeNull();
  const signIn = screen.getAllByText("Sign in to act");
  expect(signIn).toHaveLength(1);
  fireEvent.click(signIn[0]!);
  expect(signInOpen.value).toBe(true);
});

test("signed in: each card keeps its Approve button and there is no sign-in prompt", () => {
  render(<CardRail cards={cards} authed />);
  expect(screen.getAllByText("Approve")).toHaveLength(2);
  expect(screen.queryByText("Sign in to act")).toBeNull();
});
```

Append to `dashboard/test/ProjectPage.test.tsx` (reuse the same `api` mock shape as the existing tests, but make `/nodes` return one spec plus one ready task and make `/inbox` return `review: [{ id: 2, project_id: 1, parent_id: 1, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "a" }]`; set `authed.value = false` first):

```tsx
test("signed out: Run tasks is disabled with a sign-in reason and no Approve button is shown", async () => {
  authed.value = false;
  projects.value = [{ id: 1, goal: "Voxscore", phase: "executing" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "done", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null };
    const task = { id: 2, project_id: 1, parent_id: 1, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "a", body_md: "y", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null };
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ ...spec, agent: null }, { ...task, agent: "claude" }], edges: [] });
    if (path.startsWith("/nodes/2/diff")) return Promise.resolve({ node_id: 2, source: "none", diff: "", truncated: false, commits: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([spec, task]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [task], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Sign in to act"));
  expect(screen.queryByText("Approve")).toBeNull();
  expect(screen.getByText("▶ Run tasks")).toBeDisabled();
  expect(document.querySelector("[data-run-reason]")?.textContent).toMatch(/sign in/i);
});
```

- [ ] **Step 2: Run to verify failure**

Run (in `dashboard/`): `npx vitest run test/Card.test.tsx test/CardRail.test.tsx test/ProjectPage.test.tsx`
Expected: FAIL (`readOnly` ignored, `authed` prop unknown, Run enabled).

- [ ] **Step 3: Implement**

`Card.tsx`: change the signature to `export function Card({ card, onActed, readOnly = false }: { card: CardT; onActed: () => void; readOnly?: boolean })`. Wrap the three action blocks (the `card.kind === "ack" ? ... : ...` ternary, the `rejecting` block and the `discussing` block) so none render when `readOnly`:

```tsx
      {readOnly ? null : card.kind === "ack" ? (
```
(i.e. prefix the existing ternary with `readOnly ? null :`), and change `{rejecting ? (` to `{!readOnly && rejecting ? (` and `{discussing ? (` to `{!readOnly && discussing ? (`.

`CardRail.tsx`: add `authed: boolean` to props, import `signInOpen` from `../state` and `Button` from `../ui/Button`. Under the header row render, when `!authed`:

```tsx
      {authed ? null : <Button variant="plain" data-sign-in-to-act onClick={() => { signInOpen.value = true; }}>Sign in to act</Button>}
      {cards.map((c) => <Card key={c.id} card={c} readOnly={!authed} onActed={refresh} />)}
```
(If `Button` does not forward `data-*` props, put the attribute on a wrapping `<div data-sign-in-to-act>` instead.)

`ProjectPage.tsx`: pass `authed={authed.value}` to `<CardRail>`. Change the Run button and reason:

```tsx
            <Button variant={step.id === "run" ? "filled" : "outline"} busy={runA.busy} busyLabel="Starting…" disabled={!authed.value || !!reason} onClick={run}>▶ Run tasks</Button>
            {!authed.value ? <span class="caption" data-run-reason>Sign in to run tasks</span> : reason ? <span class="caption" data-run-reason>{reason}</span> : null}
```

- [ ] **Step 4: Run tests, typecheck, build**

Run: `npx vitest run && npx tsc --noEmit && npm run build`
Expected: all PASS, build writes `../src/muvue/api/static/index.html`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Signed-out view: hide card actions, disable Run, one sign-in prompt"
```
(append the trailers from Global Constraints)

---

### Task 2: The review card says what happened (flaw 3)

**Files:**
- Modify: `dashboard/src/state.ts` (NodeRow), `dashboard/src/node/diff.ts`, `dashboard/src/cards/cardsFromInbox.ts`, `dashboard/src/cards/Card.tsx`, `dashboard/src/cards/cards.css`
- Test: create `dashboard/test/diffStat.test.ts`; modify `dashboard/test/cardsFromInbox.test.ts`, `dashboard/test/Card.test.tsx`

**Interfaces:**
- Produces: `diffStat(text: string): { added: number; removed: number }` in `node/diff.ts`. `NodeRow` gains `summary?: string | null`. `Card` (type) gains optional `summary?: string; tier?: RiskTier; ownerLabel?: string`. `task_review` cards: `context` stays a string but is now the summary (or `"No summary recorded"`), and `ownerLabel` is the owner with any `runner:` prefix removed.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/diffStat.test.ts`:

```ts
import { diffStat } from "../src/node/diff";

test("counts added and removed lines, ignoring file headers", () => {
  const text = ["commit abc", "diff --git a/x b/x", "--- a/x", "+++ b/x", "@@ -1,2 +1,3 @@", " keep", "-old", "+new", "+newer"].join("\n");
  expect(diffStat(text)).toEqual({ added: 2, removed: 1 });
});

test("empty diff is zero", () => {
  expect(diffStat("")).toEqual({ added: 0, removed: 0 });
});
```

Append to `dashboard/test/cardsFromInbox.test.ts` (use the file's existing inbox fixture helper or build a minimal `Inbox` with empty arrays):

```ts
test("a review card carries the agent's summary, the risk tier and a readable owner", () => {
  const inbox = { questions: [], review: [{ id: 5, project_id: 1, parent_id: 1, kind: "task", title: "Detect pitch", status: "review", risk_tier: "medium", owner: "runner:claude", summary: "Added a YIN tracker." }], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] } as never;
  const [card] = cardsFromInbox(inbox, { revisions: [], nodesById: {}, projectId: 1 });
  expect(card).toMatchObject({ kind: "task_review", context: "Added a YIN tracker.", tier: "medium", ownerLabel: "claude" });
});

test("a review card with no summary says so instead of repeating 'in review'", () => {
  const inbox = { questions: [], review: [{ id: 5, project_id: 1, parent_id: 1, kind: "task", title: "T", status: "review", risk_tier: "low", owner: null, summary: null }], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] } as never;
  expect(cardsFromInbox(inbox, { revisions: [], nodesById: {}, projectId: 1 })[0]!.context).toBe("No summary recorded");
});
```

In `dashboard/test/Card.test.tsx` add a `beforeEach(() => { vi.restoreAllMocks(); })`-safe diff mock, and:

```tsx
test("a review card shows +N −M, the tier and an Open task link", async () => {
  vi.spyOn(client, "api").mockResolvedValue({ node_id: 5, source: "worktree", diff: "+a\n+b\n-c\n", truncated: false, commits: [] });
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base, context: "Added a tracker.", tier: "medium", ownerLabel: "claude" }} onActed={() => {}} />);
  await waitFor(() => screen.getByText("+2 −1"));
  expect(screen.getByText(/medium risk/)).toBeTruthy();
  expect(screen.getByText("Open task")).toBeTruthy();
  expect(screen.getByLabelText("owner")).toHaveTextContent("claude");
});

test("a review card says when the diff could not be loaded", async () => {
  vi.spyOn(client, "api").mockRejectedValue(new Error("boom"));
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  await waitFor(() => screen.getByText("diff unavailable"));
});
```
Existing tests in that file that render a `task_review` card will now call `api`; add `vi.spyOn(client, "api").mockResolvedValue({ source: "none", diff: "", truncated: false, commits: [] })` to their start.

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run test/diffStat.test.ts test/cardsFromInbox.test.ts test/Card.test.tsx`
Expected: FAIL (`diffStat` not exported, no `tier`/`ownerLabel`).

- [ ] **Step 3: Implement**

`node/diff.ts` (append):

```ts
export function diffStat(text: string): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const line of text.split("\n")) {
    if (/^(\+\+\+|---)/.test(line)) continue;
    if (line.startsWith("+")) added++;
    else if (line.startsWith("-")) removed++;
  }
  return { added, removed };
}
```

`state.ts`: add `summary?: string | null;` to `NodeRow`.

`cardsFromInbox.ts`: add `summary?: string; tier?: RiskTier; ownerLabel?: string` to the `Card` type (import `RiskTier` from `../state`). Replace the review line:

```ts
const ownerName = (o: string | null) => (o ?? "").replace(/^runner:/, "");
  for (const n of inbox.review) cards.push({ id: "task_review:" + n.id, kind: "task_review", nodeId: n.id, projectId: n.project_id, title: n.title, context: n.summary?.trim() || "No summary recorded", tier: n.risk_tier, ownerLabel: ownerName(n.owner), agent: "", ts: "" });
```
(define `ownerName` once above `cardsFromInbox`; `agent` becomes `""` for review cards so the old bare owner line no longer renders; blocked/awaiting cards keep their `agent`.)

`Card.tsx`: import `useApi` from `../hooks`, `api` (already via `post` import line: add `api`), `diffStat` from `../node/diff`. Add a small child component in the same file and render it for `task_review`:

```tsx
function ReviewFacts({ card }: { card: CardT }) {
  const diffQ = useApi<{ diff: string; source: string }>(() => api(routes.nodeDiff(card.nodeId!)), [card.nodeId]);
  const stat = diffQ.data && diffQ.data.source !== "none" ? diffStat(diffQ.data.diff) : null;
  return (
    <div class="review-facts caption">
      {diffQ.error ? <span>diff unavailable</span> : diffQ.data ? (stat ? <span class="mono">{`+${stat.added} −${stat.removed}`}</span> : <span>no changes recorded</span>) : <span>…</span>}
      {card.tier ? <span> · {card.tier} risk</span> : null}
      {card.ownerLabel ? <span aria-label="owner"> · {card.ownerLabel}</span> : null}
      <button type="button" class="link-btn" onClick={() => card.nodeId && openNode(card.nodeId)}>Open task</button>
    </div>
  );
}
```
In the card body replace `<div>{card.context}</div>` with `<div class="clamp-3">{card.context}</div>` and add `{card.kind === "task_review" && card.nodeId ? <ReviewFacts card={card} /> : null}` right after it. The owner `aria-label` goes on the span as written; the `"+2 −1"` text must be a single text node (template literal above does that). Drop the `{CAPTION...}` duplication: for `task_review` the caption stays "Task in review" (the context line no longer repeats it).

`cards.css` (append):

```css
.clamp-3 { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.review-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.link-btn { margin-left: auto; min-height: 44px; padding: 0 8px; background: none; border: 0; color: var(--accent-strong); cursor: pointer; font: inherit; text-decoration: underline; }
```

- [ ] **Step 4: Run tests, typecheck, build**

Run: `npx vitest run && npx tsc --noEmit && npm run build` — Expected: PASS.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Review card: agent summary, +N -M, risk tier, owner label and Open task"` (+ trailers)

---

### Task 3: Type scale, blocked/failed emphasis, contrast (flaws 7 and 8)

**Files:**
- Modify: `dashboard/src/styles/base.css`, `dashboard/src/styles/tokens.css`, `dashboard/src/canvas/canvas.css`, `dashboard/src/cards/CardRail.tsx`, `dashboard/src/canvas/TaskBox.tsx`
- Test: create `dashboard/test/type-scale.test.ts`; modify `dashboard/test/contrast.test.ts`, `dashboard/test/CardRail.test.tsx`, `dashboard/test/TaskBox.test.tsx`

**Interfaces:**
- Produces: token `--st-in_progress-strong` (light `#9A6A00`, dark `#E3B04B`); `.caption` is 13px, `.muted` 14px; no `font-size` below 12px anywhere; `.needs-you-dot` has `title="Needs your attention"`; the rail's collapse button is the text button "Hide" with `aria-label="hide needs-you list"`.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/type-scale.test.ts`:

```ts
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : []));
}
const src = join(__dirname, "..", "src");

test("no font-size in any stylesheet is below 12px", () => {
  const small: string[] = [];
  for (const f of cssFiles(src)) for (const m of readFileSync(f, "utf8").matchAll(/font-size:\s*([\d.]+)px/g)) if (Number(m[1]) < 12) small.push(`${f}: ${m[0]}`);
  expect(small).toEqual([]);
});

test("secondary text (.caption) is at least 13px", () => {
  const base = readFileSync(join(src, "styles", "base.css"), "utf8");
  const m = base.match(/\.caption\s*\{[^}]*font-size:\s*(\d+)px/);
  expect(Number(m![1])).toBeGreaterThanOrEqual(13);
});
```

In `dashboard/test/contrast.test.ts` add inside the existing `describe.each` `pairs` list (non-text graphics need 3:1, so use a separate test below instead of the 4.5 list) — append after the describe block:

```ts
describe.each([["light", light], ["dark", dark]] as const)("%s mode: graphics contrast is at least 3:1", (_mode, t) => {
  test.each([
    ["working-now dot on a surface", t["st-in_progress-strong"]!, t["surface"]!],
    ["arrow line on the canvas", t["text-2"]!, t["surface-2"]!],
  ])("%s", (_n, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(3);
  });
});
```
Append to `CardRail.test.tsx`:

```tsx
test("the collapse control is labelled 'Hide' and collapses to a count pill", () => {
  render(<CardRail cards={cards} authed />);
  fireEvent.click(screen.getByLabelText("hide needs-you list"));
  expect(screen.getByText("2 need you")).toBeTruthy();
});
```
Append to `TaskBox.test.tsx`:

```tsx
test("the needs-you dot explains itself", () => {
  const { container } = render(<TaskBox task={task} needsYou={new Set([5])} projectPhase="executing" />);
  expect(container.querySelector(".needs-you-dot")).toHaveAttribute("title", "Needs your attention");
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/type-scale.test.ts test/contrast.test.ts test/CardRail.test.tsx test/TaskBox.test.tsx`; Expected: FAIL (`.caption` 12px, missing token, old label).

- [ ] **Step 3: Implement**

`tokens.css`: add `--st-in_progress-strong: #9A6A00;` after `--st-in_progress`, and `--st-in_progress-strong: #E3B04B;` inside the dark `:root` block.

`base.css`: `.muted { ...font-size: 14px }` and `.caption { ...font-size: 13px }` (was 13/12). `.tier { font-size: 12px }` in `ui.css` becomes 13px.

`canvas.css`: `.arrow-label` font-size 12px → 13px; `.subtask-row` 12px → 13px; `.agent-tag` 12px → 13px; `.canvas-arrows path.flow-arrow` `stroke-width: 2` → `1.5`; add

```css
.agent-tag.st-running .agent-dot { background: var(--st-in_progress-strong); box-shadow: 0 0 0 2px color-mix(in srgb, var(--st-in_progress) 35%, var(--surface)); }
.task-box.st-bar-blocked { background: color-mix(in srgb, var(--st-blocked) 8%, var(--surface)); }
.task-box.st-bar-failed { background: color-mix(in srgb, var(--st-failed) 10%, var(--surface)); border-color: var(--st-failed); border-left-width: 6px; }
```
(remove the existing `.agent-tag.st-running .agent-dot { background: var(--st-in_progress); }` line so there is one rule). In `Canvas.tsx` change the marker to `markerWidth="6" markerHeight="6"`.

`TaskBox.tsx`: add `title="Needs your attention"` to `.needs-you-dot`.

`CardRail.tsx`: replace the `—` button with `<button type="button" class="btn btn-plain" aria-label="hide needs-you list" onClick={() => setCollapsed(true)}>Hide</button>`.

- [ ] **Step 4: Run tests, typecheck, build, ui-check.** Raising type sizes changes box content heights, so the ui-check "every box's content fits" check may now fail; if it does, raise `TASK_H_BASE` in `flowLayout.ts` to the measured need (Task 4 replaces that constant with an exact estimate, so a temporary bump is fine). Expected after: vitest PASS, ui-check 31/31.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Type scale floor, blocked and failed emphasis, working-now dot and arrow contrast"` (+ trailers)

---

### Task 4: Content-sized task boxes and informative subtasks (flaw 4)

**Files:**
- Modify: `dashboard/src/canvas/flowLayout.ts`, `dashboard/src/canvas/canvasData.ts`, `dashboard/src/canvas/TaskBox.tsx`, `dashboard/src/canvas/SubtaskRow.tsx`, `dashboard/src/canvas/canvas.css`, `dashboard/scripts/ui-check.mjs`
- Test: `dashboard/test/flowLayout.test.ts`, `dashboard/test/canvasData.test.ts`, `dashboard/test/TaskBox.test.tsx`

**Interfaces:**
- Produces: `taskHeight(t: { title: string; hasPurpose: boolean; hasReason: boolean; subtaskCount: number; hasProgress: boolean }): number` (replaces `taskHeight(subtaskCount)`); `CanvasSubtask` gains `owner: string | null`; `subtaskProgress(subtasks): { done: number; total: number }` exported from `canvasData.ts`.
- Height model (from the CSS: padding 10*2 + border 2 = 22; title line 21px; caption line 18px at 13px/1.4; flex gap 4): `22 + titleLines*21 + (purpose ? 4+18+8 : 0) + (reason ? 4+18 : 0) + (progress ? 4+18 : 0) + subtaskBlock + 4 + 18 + 4`, where `titleLines = min(2, max(1, ceil(title.length / 26)))` and `subtaskBlock = rows ? 12 + rows*44 : 0`.

- [ ] **Step 1: Write the failing tests**

In `flowLayout.test.ts`, replace usages of `taskHeight(n)`/`TASK_H_BASE` with the new signature and add:

```ts
import { taskHeight } from "../src/canvas/flowLayout";

const base = { title: "Short", hasPurpose: false, hasReason: false, subtaskCount: 0, hasProgress: false };

test("a one-line title with no purpose is much shorter than the old fixed 118px", () => {
  expect(taskHeight(base)).toBe(22 + 21 + 4 + 18 + 4);
});

test("a long title adds a second line, a purpose adds its row, subtasks add rows plus the n-of-m line", () => {
  expect(taskHeight({ ...base, title: "x".repeat(40) })).toBe(taskHeight(base) + 21);
  expect(taskHeight({ ...base, hasPurpose: true })).toBe(taskHeight(base) + 30);
  expect(taskHeight({ ...base, subtaskCount: 2, hasProgress: true })).toBe(taskHeight(base) + 22 + 12 + 2 * 44);
  expect(taskHeight({ ...base, subtaskCount: 5, hasProgress: true })).toBe(taskHeight(base) + 22 + 12 + 4 * 44);
});
```
In `canvasData.test.ts`:

```ts
import { subtaskProgress } from "../src/canvas/canvasData";
test("subtaskProgress counts done subtasks", () => {
  expect(subtaskProgress([{ id: 1, title: "a", status: "done", parent_id: 9, owner: null }, { id: 2, title: "b", status: "ready", parent_id: 9, owner: null }])).toEqual({ done: 1, total: 2 });
});
```
In `TaskBox.test.tsx` (update the `task` fixture's subtasks to include `owner: null`/`"claude"`):

```tsx
test("the parent shows 'n of m done' and each subtask shows a readable status", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("1 of 2 subtasks done")).toBeTruthy();
  expect(screen.getByLabelText("done: YIN tracker")).toBeTruthy();
  expect(screen.getByLabelText("ready: smoothing")).toBeTruthy();
});

test("a blocked task shows its reason", () => {
  render(<TaskBox task={{ ...task, status: "blocked", block_reason: "rate_limit" }} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Blocked: rate_limit")).toBeTruthy();
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/flowLayout.test.ts test/canvasData.test.ts test/TaskBox.test.tsx`; Expected: FAIL.

- [ ] **Step 3: Implement**

`canvasData.ts`: add `owner: string | null` to `CanvasSubtask`; in `buildCanvasData` push `owner: n.owner` into each subtask; export

```ts
export function subtaskProgress(subtasks: CanvasSubtask[]): { done: number; total: number } {
  return { done: subtasks.filter((s) => s.status === "done").length, total: subtasks.length };
}
```
Fix every other construction of `CanvasSubtask` that the typecheck flags (tests and fixtures) by adding `owner: null`.

`flowLayout.ts`: replace the long comment and `taskHeight`:

```ts
// Heights come from the content, derived from the built CSS (15px/1.4 title =
// 21px a line; 13px/1.4 caption = 18px; 10px padding + 1px border a side;
// 4px flex gap). The title-line count is an estimate from its length (the box
// fits about 26 characters a line), so ui-check's "content fits" and "no dead
// space" checks verify it in a real browser: fix the estimate, never hand-tune
// one box.
export const NODE_W = 240, SPEC_H = 112, SUBTASK_LIST_EXTRA = 12, SUBTASK_ROW_H = 44, MAX_SUBTASK_ROWS = 3, PLACEHOLDER_H = 140, PAD = 16, GAP_X = 24, GAP_Y = 72;
const BOX_CHROME = 22, TITLE_LINE = 21, CAPTION_LINE = 18, FLEX_GAP = 4, PURPOSE_MARGIN = 8, SLACK = 4;

export function taskHeight(t: { title: string; hasPurpose: boolean; hasReason: boolean; subtaskCount: number; hasProgress: boolean }): number {
  const titleLines = Math.min(2, Math.max(1, Math.ceil(t.title.length / 26)));
  const rows = Math.min(t.subtaskCount, MAX_SUBTASK_ROWS) + (t.subtaskCount > MAX_SUBTASK_ROWS ? 1 : 0);
  return (
    BOX_CHROME + titleLines * TITLE_LINE
    + (t.hasPurpose ? FLEX_GAP + CAPTION_LINE + PURPOSE_MARGIN : 0)
    + (t.hasReason ? FLEX_GAP + CAPTION_LINE : 0)
    + (t.hasProgress ? FLEX_GAP + CAPTION_LINE : 0)
    + (rows ? SUBTASK_LIST_EXTRA + rows * SUBTASK_ROW_H : 0)
    + FLEX_GAP + CAPTION_LINE + SLACK
  );
}
```
Remove `TASK_H_BASE` (and its temporary Task 3 bump). In `computeLayout` set

```ts
heightOf[t.id] = taskHeight({ title: t.title, hasPurpose: !!purposeLine(t.body_md), hasReason: t.status === "blocked" && !!t.block_reason, subtaskCount: t.subtasks.length, hasProgress: t.subtasks.length > 0 });
```
(import `purposeLine` from `./canvasData`).

`TaskBox.tsx`: after the purpose line add

```tsx
      {task.status === "blocked" && task.block_reason ? <div class="caption reason">Blocked: {task.block_reason}</div> : null}
      {task.subtasks.length ? <div class="caption progress-line">{subtaskProgress(task.subtasks).done} of {task.subtasks.length} subtasks done</div> : null}
```
(import `subtaskProgress`). 

`SubtaskRow.tsx`: real status glyphs with a readable name: make GLYPH `{ done: "✓", in_progress: "◐", review: "◆", pending: "○", ready: "○", blocked: "■", failed: "✕", awaiting_approval: "◆" }`, render

```tsx
<button type="button" class="subtask-row" aria-label={`${subtask.status.replace("_", " ")}: ${subtask.title}`} onClick={...}>
  <span class="glyph" aria-hidden="true" style={{ color: `var(--st-${subtask.status})` }}>{GLYPH[subtask.status] ?? "○"}</span>
  <span class="grow title">{subtask.title}</span>
  {subtask.owner ? <span class="caption subtask-owner">{subtask.owner.replace(/^runner:/, "")}</span> : null}
  {needsYou ? <span class="needs-you-dot" title="Needs your attention" aria-label="needs you" /> : null}
</button>
```
(`aria-label` must equal `done: YIN tracker` / `ready: smoothing` for the tests; `status.replace("_"," ")` gives `in progress`.) `canvas.css`: `.subtask-owner { flex: none; max-width: 6rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`, `.progress-line, .reason { margin: 0; }`, `.task-box .reason { color: color-mix(in srgb, var(--st-blocked) 80%, var(--text)); }`.

`ui-check.mjs`: inside `dagChecks` after the overflow check add

```js
  const deadSpace = await page.locator(".task-box:not(.dag-empty)").evaluateAll((els) => els.map((e) => {
    const tag = e.querySelector(".agent-tag");
    const prev = tag?.previousElementSibling;
    const gap = tag && prev ? tag.getBoundingClientRect().top - prev.getBoundingClientRect().bottom : 0;
    return { title: e.querySelector(".title")?.textContent ?? "?", gap: Math.round(gap) };
  }).filter((b) => b.gap > 20).map((b) => `${b.title}: ${b.gap}px`));
  check(`${label}: no task box has dead space above its agent tag`, deadSpace.length === 0, deadSpace.join("; "));
```

- [ ] **Step 4: Run** — `npx vitest run && npx tsc --noEmit && npm run build`, then ui-check. If "content fits" fails, correct the estimate constants above to the measured values and keep the unit-test expectations in sync; if "dead space" fails, same. Expected: PASS and 33/33 (31 + 2 new: dead-space at phone and desktop).

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Size task boxes to their content; subtask status glyphs, owner and n of m done"` (+ trailers)

---

### Task 5: A diagram that scales to 40 tasks (flaw 5)

**Files:**
- Create: `dashboard/src/canvas/pan.ts`
- Modify: `dashboard/src/canvas/Canvas.tsx`, `dashboard/src/canvas/canvasData.ts`, `dashboard/src/canvas/canvas.css`, `dashboard/scripts/ui-check.mjs`
- Test: create `dashboard/test/pan.test.ts`, `dashboard/test/collapseDone.test.ts`; modify `dashboard/test/Canvas.test.tsx`

**Interfaces:**
- Produces: `clampPan(pan: {x:number;y:number}, content: {w:number;h:number}, view: {w:number;h:number}): {x:number;y:number}` (content is already scaled; an axis whose content fits the view is pinned to 0). `collapseDone(data: CanvasData, expanded: boolean): { data: CanvasData; hiddenDone: number }` and `COLLAPSE_DONE_OVER = 12` in `canvasData.ts`. Finished tasks are hidden only when the project has more than 12 tasks.
- Out of scope, stated on purpose: a horizontal layout for long chains. The fixed-height pan/zoom viewport and collapsed finished tasks remove the page-length problem; a second layout mode is a separate change.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/pan.test.ts`:

```ts
import { clampPan } from "../src/canvas/pan";

test("an axis whose content fits the view is pinned to zero", () => {
  expect(clampPan({ x: 50, y: -80 }, { w: 300, h: 200 }, { w: 400, h: 500 })).toEqual({ x: 0, y: 0 });
});

test("a taller-than-view axis can pan only between the top and the bottom edge", () => {
  expect(clampPan({ x: 0, y: 40 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: 0 });
  expect(clampPan({ x: 0, y: -5000 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: -1500 });
  expect(clampPan({ x: 0, y: -700 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: -700 });
});
```
`dashboard/test/collapseDone.test.ts`:

```ts
import { collapseDone, COLLAPSE_DONE_OVER, type CanvasData, type CanvasTask } from "../src/canvas/canvasData";

const t = (id: number, status: CanvasTask["status"]): CanvasTask => ({ id, title: "t" + id, status, risk_tier: "low", owner: null, agent: "claude", body_md: null, criteria_hash: null, block_reason: null, subtasks: [] });
const data = (n: number, doneCount: number): CanvasData => ({
  spec: { id: 1, title: "s", body_md: null, status: "done", agent: null },
  tasks: Array.from({ length: n }, (_, i) => t(i + 10, i < doneCount ? "done" : "ready")),
  edges: [{ from: 10, to: n > 1 ? 11 : 10, carries: null }],
});

test("small projects are never collapsed", () => {
  const d = data(COLLAPSE_DONE_OVER, 5);
  expect(collapseDone(d, false)).toEqual({ data: d, hiddenDone: 0 });
});

test("a big project hides finished tasks and the edges touching them", () => {
  const { data: out, hiddenDone } = collapseDone(data(20, 12), false);
  expect(hiddenDone).toBe(12);
  expect(out.tasks).toHaveLength(8);
  expect(out.edges).toHaveLength(0);
});

test("expanded shows everything", () => {
  const d = data(20, 12);
  expect(collapseDone(d, true).hiddenDone).toBe(0);
});
```
Append to `Canvas.test.tsx` (mirror its existing data fixture; build 14 tasks, 13 `done`):

```tsx
test("with more than 12 tasks the finished ones fold into a '13 done' toggle that expands them", () => {
  const tasks = Array.from({ length: 14 }, (_, i) => ({ id: i + 10, title: "T" + i, status: i < 13 ? "done" : "ready", risk_tier: "low", owner: null, agent: "claude", body_md: null, criteria_hash: null, block_reason: null, subtasks: [] })) as never;
  const data = { spec: { id: 1, title: "S", body_md: null, status: "done", agent: null }, tasks, edges: [] };
  const { container } = render(<Canvas data={data} projectId={1} projectPhase="executing" needsYou={new Set()} activity={null} />);
  expect(container.querySelectorAll(".task-box")).toHaveLength(1);
  fireEvent.click(screen.getByText("13 done"));
  expect(container.querySelectorAll(".task-box")).toHaveLength(14);
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/pan.test.ts test/collapseDone.test.ts test/Canvas.test.tsx`; Expected: FAIL (modules missing).

- [ ] **Step 3: Implement**

`pan.ts`:

```ts
type Pt = { x: number; y: number };
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function clampPan(pan: Pt, content: { w: number; h: number }, view: { w: number; h: number }): Pt {
  return {
    x: content.w <= view.w ? 0 : clamp(pan.x, view.w - content.w, 0),
    y: content.h <= view.h ? 0 : clamp(pan.y, view.h - content.h, 0),
  };
}
```
`canvasData.ts`: add the `COLLAPSE_DONE_OVER` and `collapseDone` exactly as:

```ts
export const COLLAPSE_DONE_OVER = 12;

export function collapseDone(data: CanvasData, expanded: boolean): { data: CanvasData; hiddenDone: number } {
  if (expanded || data.tasks.length <= COLLAPSE_DONE_OVER) return { data, hiddenDone: 0 };
  const hidden = new Set(data.tasks.filter((t) => t.status === "done").map((t) => t.id));
  if (!hidden.size) return { data, hiddenDone: 0 };
  return {
    data: { ...data, tasks: data.tasks.filter((t) => !hidden.has(t.id)), edges: data.edges.filter((e) => !hidden.has(e.from) && !hidden.has(e.to)) },
    hiddenDone: hidden.size,
  };
}
```
`Canvas.tsx`: rename the prop `data` to `fullData` inside and compute

```tsx
  const [showDone, setShowDone] = useState(false);
  const { data, hiddenDone } = collapseDone(fullData, showDone);
```
(keep the exported prop name `data`: destructure as `{ data: fullData, ... }`). Track the viewport height next to the width: `const [viewportHeight, setViewportHeight] = useState<number | null>(null);` set in the same `useLayoutEffect` (`setViewportHeight(el.clientHeight)` and in the observer `entry.contentRect.height`). Replace the pan handling:

```tsx
  const content = { w: lay.width * z, h: lay.height * z };
  const view = { w: viewportWidth ?? content.w, h: viewportHeight ?? content.h };
  const tallerThanView = content.h > view.h + 1;
  const canPan = zoomedIn || tallerThanView;
  const setPanClamped = (p: Point) => setPan(clampPan(p, content, view));
```
Use `canPan` instead of `zoomedIn` in `onPointerDown` (`if (!canPan) return;`) and in the `touchAction` (`canPan ? "none" : "pan-y"`); replace every `setPan({ x: e.clientX - ..., ... })` in `onPointerMove` with `setPanClamped(...)`. In `onWheel` add, before the `ctrlKey` return: if `!e.ctrlKey && tallerThanView` then `const next = clampPan({ x: pan.x, y: pan.y - e.deltaY }, content, view); if (next.y !== pan.y) { e.preventDefault(); setPan(next); } return;` so the page still scrolls once the diagram hits an edge. Render, above `.canvas-viewport`, when `hiddenDone > 0 || showDone && fullData.tasks.length > COLLAPSE_DONE_OVER`:

```tsx
      {hiddenDone > 0 ? <button type="button" class="canvas-done-toggle" onClick={() => setShowDone(true)}>{hiddenDone} done</button> : showDone && fullData.tasks.length > COLLAPSE_DONE_OVER ? <button type="button" class="canvas-done-toggle" onClick={() => setShowDone(false)}>Hide finished</button> : null}
```
Import `collapseDone`, `COLLAPSE_DONE_OVER` and `clampPan`. The Fit button and `−` keep resetting pan to `{0,0}`.

`canvas.css`: make the viewport fixed-height and scroll-contained:

```css
.canvas-viewport { position: relative; width: 100%; overflow: hidden; max-height: min(72dvh, 760px); }
.canvas-done-toggle { display: block; margin: 8px; min-height: 44px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; font-size: 13px; }
```
(replace the existing `.canvas-viewport` rule). The inline `height: Math.ceil(lay.height * z)` stays: CSS `max-height` caps it.

`ui-check.mjs`: add a second seeded project is heavy, so instead add a check on the existing 3-task diagram that the wrapper respects the cap, and one synthetic long-diagram check: after the phone `dagChecks`, evaluate `document.querySelector(".canvas-viewport").getBoundingClientRect().height <= 0.72 * innerHeight + 1` → `check("phone: the diagram viewport never exceeds 72% of the screen height", ...)`. For the 40-task claim, seed 40 tasks in a second throwaway project through the same `muvue` CLI helpers the file already uses (`muvue("task", ...)` or the equivalent add-task command; run `uv run muvue --help` to find it), open it, and check: page `scrollHeight <= 2 * innerHeight` and `.canvas-viewport` height `<= 0.72 * innerHeight + 1` and a "done" toggle exists once 13+ are marked done. If marking 13 tasks done through the CLI is not practical, assert only the first two and say so in the task report.

- [ ] **Step 4: Run** — `npx vitest run && npx tsc --noEmit && npm run build`, then ui-check. Expected: PASS; report the page height before/after for the 40-task seed.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Diagram: fixed-height pan and zoom viewport, finished tasks fold into a done toggle"` (+ trailers)

---

### Task 6: Project progress bar, spend, and a dismissible demo banner (flaw 6)

**Files:**
- Create: `dashboard/src/project/ProgressBar.tsx`, `dashboard/src/project/spend.ts`
- Modify: `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/project/FakeAgentNotice.tsx`, `dashboard/src/canvas/canvas.css`
- Test: create `dashboard/test/ProgressBar.test.tsx`, `dashboard/test/spend.test.ts`; modify `dashboard/test/ProjectPage.test.tsx`

**Interfaces:**
- Produces: `progressCounts(tasks: {status: string}[]): { done; running; review; blocked; total }` and `<ProgressBar counts spend />` (`spend: string | null`); `spendLabel(kpis: { spend_by_driver: Record<string, { unit: string; spent: number; limit: number; pct: number }> } | null): string | null`. Mapping: running = `in_progress`; review = `review` or `awaiting_approval`; blocked = `blocked` or `failed`. The spend label names the driver with the highest `pct`, e.g. `$3.20 of $10.00 (claude)`; `null` when no driver has a budget. Only drivers with a configured `[agents.<x>.budget]` appear in `/kpis` today, so projects with no budget show no spend. The plan adds no backend field on purpose.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/spend.test.ts`:

```ts
import { spendLabel } from "../src/project/spend";

test("no budgets configured means no spend label", () => {
  expect(spendLabel({ spend_by_driver: {} })).toBeNull();
  expect(spendLabel(null)).toBeNull();
});

test("names the driver closest to its limit and formats usd", () => {
  const k = { spend_by_driver: { a: { unit: "usd", spent: 1, limit: 10, pct: 0.1 }, b: { unit: "usd", spent: 3.2, limit: 10, pct: 0.32 } } };
  expect(spendLabel(k)).toBe("$3.20 of $10.00 (b)");
});

test("non-usd units are printed with their unit", () => {
  expect(spendLabel({ spend_by_driver: { c: { unit: "tokens", spent: 1200, limit: 5000, pct: 0.24 } } })).toBe("1200 of 5000 tokens (c)");
});
```
`dashboard/test/ProgressBar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/preact";
import { ProgressBar, progressCounts } from "../src/project/ProgressBar";

test("counts map statuses to done, running, review and blocked", () => {
  const c = progressCounts([{ status: "done" }, { status: "in_progress" }, { status: "review" }, { status: "awaiting_approval" }, { status: "failed" }, { status: "ready" }]);
  expect(c).toEqual({ done: 1, running: 1, review: 2, blocked: 1, total: 6 });
});

test("the bar is one image with a full text alternative, and spend sits beside it", () => {
  render(<ProgressBar counts={{ done: 2, running: 1, review: 1, blocked: 0, total: 5 }} spend="$3.20 of $10.00 (claude)" />);
  expect(screen.getByRole("img")).toHaveAccessibleName("2 done, 1 running, 1 in review, 0 blocked, of 5 tasks");
  expect(screen.getByText("$3.20 of $10.00 (claude)")).toBeTruthy();
});

test("no tasks renders nothing", () => {
  const { container } = render(<ProgressBar counts={{ done: 0, running: 0, review: 0, blocked: 0, total: 0 }} spend={null} />);
  expect(container.firstChild).toBeNull();
});
```
Append to `ProjectPage.test.tsx` (clear `localStorage` in the tests that touch the banner, and add `if (path === "/kpis") return Promise.resolve({ spend_by_driver: {} })` to the mocks that need it, since the page will now fetch it):

```tsx
test("the demo notice is a banner that can be dismissed and stays dismissed", async () => {
  localStorage.clear();
  // same fake-agent mocks as the existing fake-notice test
  ...
  const { unmount } = render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  fireEvent.click(screen.getByLabelText("dismiss demo notice"));
  expect(document.querySelector("[data-fake-notice]")).toBeNull();
  unmount();
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Voxscore"));
  expect(document.querySelector("[data-fake-notice]")).toBeNull();
});
```
(Replace the `...` by copying the mock setup from the existing "shows fake-agent notice" test.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/spend.test.ts test/ProgressBar.test.tsx test/ProjectPage.test.tsx`; Expected: FAIL.

- [ ] **Step 3: Implement**

`spend.ts`:

```ts
export type Kpis = { spend_by_driver: Record<string, { unit: string; spent: number; limit: number; pct: number }> };

const fmt = (unit: string, n: number) => (unit === "usd" ? `$${n.toFixed(2)}` : String(n));

export function spendLabel(kpis: Kpis | null): string | null {
  const drivers = Object.entries(kpis?.spend_by_driver ?? {});
  if (!drivers.length) return null;
  const [name, d] = drivers.reduce((a, b) => (b[1].pct > a[1].pct ? b : a));
  return d.unit === "usd" ? `${fmt("usd", d.spent)} of ${fmt("usd", d.limit)} (${name})` : `${d.spent} of ${d.limit} ${d.unit} (${name})`;
}
```
`ProgressBar.tsx`:

```tsx
export type Counts = { done: number; running: number; review: number; blocked: number; total: number };

export function progressCounts(tasks: { status: string }[]): Counts {
  const n = (...s: string[]) => tasks.filter((t) => s.includes(t.status)).length;
  return { done: n("done"), running: n("in_progress"), review: n("review", "awaiting_approval"), blocked: n("blocked", "failed"), total: tasks.length };
}

export function ProgressBar({ counts, spend }: { counts: Counts; spend: string | null }) {
  if (!counts.total) return null;
  const pct = (n: number) => (n / counts.total) * 100 + "%";
  const label = `${counts.done} done, ${counts.running} running, ${counts.review} in review, ${counts.blocked} blocked, of ${counts.total} tasks`;
  return (
    <div class="progress-row">
      <div class="progress-seg" role="img" aria-label={label}>
        <span class="seg seg-done" style={{ width: pct(counts.done) }} />
        <span class="seg seg-running" style={{ width: pct(counts.running) }} />
        <span class="seg seg-review" style={{ width: pct(counts.review) }} />
        <span class="seg seg-blocked" style={{ width: pct(counts.blocked) }} />
      </div>
      {spend ? <span class="caption progress-spend">{spend}</span> : null}
    </div>
  );
}
```
`canvas.css` (append):

```css
.progress-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.progress-seg { display: flex; flex: 1 1 10rem; height: 10px; border-radius: 5px; overflow: hidden; background: var(--surface-2); border: 1px solid var(--border); }
.seg { display: block; height: 100%; }
.seg-done { background: var(--st-done); } .seg-running { background: var(--st-in_progress-strong); } .seg-review { background: var(--st-review); } .seg-blocked { background: var(--st-blocked); }
.demo-banner { display: flex; align-items: center; gap: 8px; padding: 6px 6px 6px 14px; border-radius: var(--radius); font-size: 13px; background: color-mix(in srgb, var(--st-in_progress) 16%, var(--surface)); border: 1px solid color-mix(in srgb, var(--st-in_progress) 45%, var(--surface)); }
.demo-banner p { margin: 0; flex: 1; }
```
Delete the now-unused `.demo-notice` rules from `canvas.css`.

`FakeAgentNotice.tsx` (rewrite; the wording keeps "demo agent" because the existing test matches it):

```tsx
import { useState } from "preact/hooks";

const KEY = "muvue.demoNoticeDismissed";

function wasDismissed(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

// Per-viewer convenience only; a blocked storage just means the banner returns.
export function FakeAgentNotice() {
  const [hidden, setHidden] = useState(wasDismissed);
  if (hidden) return null;
  function dismiss() {
    try { localStorage.setItem(KEY, "1"); } catch { /* storage blocked: the banner will show again next visit */ }
    setHidden(true);
  }
  return (
    <div class="demo-banner" data-fake-notice role="note">
      <p>Demo agent: tasks go to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</p>
      <button type="button" class="icon-btn" aria-label="dismiss demo notice" onClick={dismiss}>✕</button>
    </div>
  );
}
```
`ProjectPage.tsx`: import `ProgressBar, progressCounts` and `spendLabel, type Kpis`; add `const kpisQ = useApi<Kpis>(() => api(routes.kpis()), [refreshTick.value]);`. Move `{hasFakeAgent ? <FakeAgentNotice /> : null}` out of the `meta-row` to the first child of `<header class="project-head">`. Under the `StatusLine` button add `<ProgressBar counts={progressCounts((nodesQ.data ?? []).filter((n) => n.kind === "task"))} spend={spendLabel(kpisQ.data)} />`. Add `"/kpis"` returns to any existing ProjectPage test mocks that would otherwise resolve `{}` (that resolves to no `spend_by_driver`; `spendLabel` uses `?? {}` so `{}` is safe).

- [ ] **Step 4: Run** — `npx vitest run && npx tsc --noEmit && npm run build`, then ui-check. Expected: PASS.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Project progress bar with spend; demo notice becomes a dismissible banner"` (+ trailers)

---

### Task 7: Diagram stays in the top half on a phone when something needs you (flaw 2) and one primary action (flaw 9c)

**Files:**
- Modify: `dashboard/src/cards/CardRail.tsx`, `dashboard/src/cards/cards.css`, `dashboard/src/canvas/canvas.css`, `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/project/NextStepBar.tsx`, `dashboard/scripts/ui-check.mjs`
- Test: `dashboard/test/CardRail.test.tsx`, `dashboard/test/NextStepBar.test.tsx`

**Interfaces:**
- Produces: `CardRail` starts collapsed to a single 44px chip row ("N need you") when `window.matchMedia("(max-width: 899px)").matches`, expanded otherwise. The Next-step card keeps its filled button; on phones its detail paragraph is clamped to one line. The toolbar's `▶ Run tasks` is `outline` unless `step.id === "run"` (existing) AND no card needs you; otherwise the Next card or a card's Approve stays the single filled action.

- [ ] **Step 1: Write the failing tests**

Append to `CardRail.test.tsx`:

```tsx
function phone(matches: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches, media: q, addEventListener() {}, removeEventListener() {} }));
}
afterEach(() => vi.unstubAllGlobals());

test("on a phone the rail starts as a one-line chip and expands on tap", () => {
  phone(true);
  render(<CardRail cards={cards} authed />);
  expect(screen.queryByText("Approve")).toBeNull();
  fireEvent.click(screen.getByText("2 need you"));
  expect(screen.getAllByText("Approve")).toHaveLength(2);
});

test("on a desktop the rail starts expanded", () => {
  phone(false);
  render(<CardRail cards={cards} authed />);
  expect(screen.getAllByText("Approve")).toHaveLength(2);
});
```
Append to `NextStepBar.test.tsx` a test that the bar renders its detail inside an element with class `next-detail` (so CSS can clamp it):

```tsx
test("the detail paragraph is clampable on phones", () => {
  const { container } = render(<NextStepBar step={{ id: "run", title: "Run the tasks", detail: "2 tasks are ready." } as never} authed projectId={1} specId={1} activity={null} onAddTask={() => {}} />);
  expect(container.querySelector("p.next-detail")).toBeTruthy();
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/CardRail.test.tsx test/NextStepBar.test.tsx`; Expected: FAIL.

- [ ] **Step 3: Implement**

`CardRail.tsx`: initial state `const [collapsed, setCollapsed] = useState(() => typeof matchMedia === "function" && matchMedia("(max-width: 899px)").matches);`. `cards.css`: the pill stops floating on phones and becomes a full-width row:

```css
@media (max-width: 899px) {
  .card-rail-pill { position: static; align-self: stretch; margin: 0 16px 12px; text-align: left; }
}
```
`NextStepBar.tsx`: `<p class="muted next-detail">`. `canvas.css`: 

```css
@media (max-width: 899px) {
  .next-step { padding: 10px 14px; margin-bottom: 8px; }
  .next-step .caption { display: none; }
  .next-step h2 { margin: 0 0 2px; font-size: 16px; }
  .next-step .next-detail { margin: 0 0 6px; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 1; overflow: hidden; }
  .project-head { padding-top: 8px; gap: 4px; }
}
```
`ProjectPage.tsx`: `const runIsPrimary = step.id === "run" && cards.length === 0;` and `variant={runIsPrimary ? "filled" : "outline"}` on the Run button.

`ui-check.mjs`: extend the seed so a node is in review, then assert the claim. After the spec approval and before `serve`, nothing exists to review yet (tasks appear via the UI planning step), so do it after `03-phone-after-plan`: use the CLI to start and complete one of the planned tasks with a summary (find the commands with `uv run muvue --help`; `muvue start <node>` then `muvue done <node> --summary "Added the tracker."` or the closest real equivalents), then reload the phone page and screenshot `03b-phone-with-review.png`. Replace the existing "top half" assertion so it runs in this state: 

```js
  check("phone: with a review item waiting, the diagram still starts in the top 60% of the screen", dagTopPhoneReview < 0.6 * 844, `starts at ${Math.round(dagTopPhoneReview)}px`);
  check("phone: the review card is a one-line chip until tapped", (await page.locator(".card-rail-pill").count()) === 1 && (await page.locator(".notif-card").count()) === 0);
```
If the measured start is at or above 0.6 * 844 after the CSS above, tighten the CSS (hide the toolbar caption on phones, reduce the progress-row gap) until it passes, and report the measured number. Do not relax the threshold without telling the controller. If no CLI path can put a node into `review`, stop and report BLOCKED rather than skipping the seed.

- [ ] **Step 4: Run** — `npx vitest run && npx tsc --noEmit && npm run build`, then ui-check. Expected: PASS with the new checks.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html && git commit -m "Phone: collapse Needs-you to a chip, compact the Next card, one primary action; ui-check seeds a review item"` (+ trailers)

---

### Task 8: Small fixes, decision record, changelog, full verification (flaw 9)

**Files:**
- Modify: `dashboard/src/shell/Sidebar.tsx`, `dashboard/src/shell/TopBar.tsx`, `dashboard/src/shell/shell.css`, `docs/decisions.md`, `CHANGELOG.md`
- Test: `dashboard/test/shell.test.tsx`

**Interfaces:**
- Produces: sidebar project buttons and the phone header project name carry `title={goal}`; the sidebar is `position: sticky; top: 0; height: 100dvh; overflow-y: auto` so it never ends before the page does (the existing rule already sticks it at viewport height, so this task verifies and adds only `overflow-y: auto` for many projects).

- [ ] **Step 1: Write the failing tests** — append to `shell.test.tsx` (use the file's existing state setup for `projects`):

```tsx
test("project names that get truncated carry a tooltip with the full goal", () => {
  projects.value = [{ id: 1, goal: "voxscore demo: record voices, transcribe to sheet music", phase: "executing" }];
  projectId.value = 1;
  const { container } = render(<Sidebar />);
  expect(container.querySelector(".nav-item .grow")).toHaveAttribute("title", "voxscore demo: record voices, transcribe to sheet music");
});

test("the phone header project name has the full goal as a tooltip", () => {
  projects.value = [{ id: 1, goal: "voxscore demo: record voices, transcribe to sheet music", phase: "executing" }];
  projectId.value = 1;
  const { container } = render(<TopBar onSearch={() => {}} />);
  expect(container.querySelector(".project-goal")).toHaveAttribute("title", "voxscore demo: record voices, transcribe to sheet music");
});
```
(import `Sidebar`, `TopBar`, `projects`, `projectId` if the file does not already.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run test/shell.test.tsx`; Expected: FAIL.

- [ ] **Step 3: Implement**

`Sidebar.tsx`: `<span class="grow" title={pr.goal}>{pr.goal}</span>`. `TopBar.tsx`: `<span class="grow project-goal" title={p ? p.goal : undefined}>`. `shell.css`: add `overflow-y: auto;` to the desktop `.sidebar` rule.

`docs/decisions.md`: append decision 175 after #174 in the file's existing numbered style (plain prose; note the existing entries' format first with `sed -n 2408,2440p docs/decisions.md`). Content: the dashboard review's nine fixes; card actions and Run are hidden/disabled when signed out with one sign-in prompt; review cards show the agent's summary, a client-counted `+N −M` from `GET /nodes/{id}/diff`, tier and owner; task-box heights are estimated from content and verified by ui-check; the canvas viewport is capped at `min(72dvh, 760px)` and pans inside itself, finished tasks fold away above 12 tasks; progress is a segmented bar with spend from `GET /kpis` `spend_by_driver` (shown only when a budget is configured, so there is no per-project spend without one); the demo notice is a dismissible banner remembered in `localStorage`; type floor 12px with `.caption` 13px. State explicitly: #174 still holds (the Next-step bar carries the one filled action, and on phones the Run button stays outline while any card needs you) and #173 is unchanged (this adds no request that extends a session). Note the horizontal layout for long chains was not built. `CHANGELOG.md`: under `## [Unreleased]` add a `### Changed` bullet per user-visible change (signed-out read-only view, review card facts, content-sized boxes and subtask status, scrollable diagram with folded finished tasks, progress bar and spend, phone layout, type scale and contrast, tooltips).

- [ ] **Step 4: Full verification** — run and read the output of each: `cd dashboard && npx vitest run && npx tsc --noEmit && npm run build`; from the repo root `git status --short` (only the intended files; the rebuilt `src/muvue/api/static/index.html` committed); the ui-check command from Global Constraints (expect all checks PASS; record the final count); `uv run pytest -q` (expect 977 passed; `tests/test_dashboard_static.py` checks the built bundle and `routes.ts` paths). Report every number. If any check fails, fix the cause; do not loosen an assertion.

- [ ] **Step 5: Commit** — `git add dashboard src/muvue/api/static/index.html docs/decisions.md CHANGELOG.md && git commit -m "Tooltips for truncated project names, decision 175 and changelog for the UI fixes"` (+ trailers)

---

## Self-Review

- **Spec coverage:** flaw 1 → Task 1; flaw 2 → Task 7 (plus the Task 7 ui-check seed); flaw 3 → Task 2; flaw 4 → Task 4; flaw 5 → Task 5 (horizontal layout explicitly out of scope); flaw 6 → Task 6; flaw 7 → Task 3 (plus Task 4 blocked reason line); flaw 8 → Task 3; flaw 9 → Tasks 7 (primary muddiness) and 8 (tooltips, sidebar). The "not verified" list in the diagnosis is not addressed here.
- **Placeholder scan:** the two spots that depend on CLI discovery (Task 5's 40-task seed, Task 7's review seed) name the exact fallback behaviour (report, or BLOCKED) instead of leaving it open.
- **Type consistency:** `taskHeight` takes the object form in Tasks 4 and its callers; `CanvasSubtask.owner` added in Task 4 and used by `SubtaskRow`; `Card.tier/ownerLabel/summary` defined in Task 2 and consumed in the same task; `CardRail` `authed` prop (Task 1) used by Tasks 3 and 7 tests; `--st-in_progress-strong` token (Task 3) used by Task 6 CSS.
