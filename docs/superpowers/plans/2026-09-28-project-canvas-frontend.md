# Project Canvas Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's Plan/Inbox/Activity/Spend tabs with a single per-project canvas — a flow diagram of the spec and its tasks/subtasks, agent transparency chips, and notification cards — built on the already-merged project-canvas-backend API.

**Architecture:** A new `canvas/` module renders the flow diagram (web: pan/zoom SVG+HTML; phone: a flattened vertical `PhoneFlow`) from a pure `canvasData`/`flowLayout` pipeline. A new `cards/` module replaces the Inbox page with a notification rail built by a pure `cardsFromInbox` mapping. `project/ProjectPage.tsx` becomes the app's only page; the node sheet gains Runs/Flow/Discussion/Details sections. The `plan/`, `inbox/`, `activity/`, `spend/`, `spec/` directories are deleted.

**Tech Stack:** Preact + `@preact/signals` (unchanged), Vitest, the existing `dashboard/` Vite build producing the single committed `src/muvue/api/static/index.html`.

**Spec:** `docs/superpowers/specs/2026-09-27-project-canvas-design.md` (frontend sections: Screen layout, The canvas, Creating from the page, Agent breakdown, Agent transparency, Notification cards, Node sheet, Frontend structure, Error handling, Testing). The backend half of this same spec (schema, API endpoints, `muvue _breakdown`) is already implemented and merged (`main` @ `0c71b4d`, released as muvue 0.2.4) — this plan only touches `dashboard/` and, where a route needs a small addition the frontend spec assumed but the backend doesn't expose, `src/muvue/api/app.py`.

## Global Constraints

- Single self-contained `src/muvue/api/static/index.html` built from `dashboard/`; `tests/test_dashboard_static.py` must keep passing (no CDN, no inline handlers, no persistent token storage, `test_every_route_in_routes_ts_is_served`).
- `routes.ts` is the only module that spells an API path (`dashboard/test/no-stray-fetch.test.ts`); `fetch(` only in `api/client.ts`/`api/auth.ts`.
- No web storage (`localStorage`/`sessionStorage`/`indexedDB`/cookies) for the token. No `alert`/`confirm`/`prompt`. No inline event-handler attributes in markup.
- 44 px minimum touch target on every tappable control (`dashboard/test/touch-targets.test.ts` pattern: a CSS rule with `min-height: 44px`).
- Mutating requests are JSON and carry `X-Request-Id` where the API supports it (`post()` already sets `Content-Type: application/json`; `X-Request-Id` is added per-call where dedupe matters, matching the existing `client.ts` contract — no client change needed, callers pass it via `init.headers`).
- Claude palette tokens in `dashboard/src/styles/tokens.css`, light and dark, unchanged.
- The canvas is the only project view: no Plan/Inbox/Activity/Spend tabs, no separate spec page (spec content moves into the spec node's sheet).
- Column = longest path over `deps` edges among `task`-kind nodes only; box width 240 px, column gap 72 px, row gap 24 px (spec: "The canvas" → "Layout (web)").
- Arrows are orthogonal (right-angle routed), not curves; parent edges are never drawn (hierarchy is shown by nesting).

---

## File Structure

New:
- `dashboard/src/canvas/canvasData.ts` — pure: merges `/graph` + `/nodes` into typed `CanvasData` (spec, tasks with nested subtasks, dep edges).
- `dashboard/src/canvas/flowLayout.ts` — pure: columns, box sizes, arrow routes/labels, phone step grouping.
- `dashboard/src/canvas/pending.ts` — signals-based registry of in-flight creates/breakdowns, for ghosts and spinners.
- `dashboard/src/canvas/AgentChip.tsx` + `agentState.ts` — pure state derivation + the chip component.
- `dashboard/src/canvas/SpecRoot.tsx`, `TaskBox.tsx`, `SubtaskRow.tsx`, `GhostBox.tsx`, `AddForm.tsx`.
- `dashboard/src/canvas/Canvas.tsx` — web pan/zoom SVG+HTML viewport.
- `dashboard/src/canvas/PhoneFlow.tsx` — phone vertical rendering.
- `dashboard/src/canvas/canvas.css`.
- `dashboard/src/cards/cardsFromInbox.ts` — pure mapping from `/inbox` (+ spec/gate2/revision context) to typed `Card[]`.
- `dashboard/src/cards/CardRail.tsx`, `Card.tsx`, `Discuss.tsx`, `cards.css`.
- `dashboard/src/project/ProjectPage.tsx`, `StatusLine.tsx`, `NewProject.tsx`, `AgentsSheet.tsx`, `HistorySheet.tsx`.
- `dashboard/src/node/Runs.tsx`, `Flow.tsx`, `Discussion.tsx`, `Details.tsx`, `SpecBody.tsx` (the line-commented spec text, moved from `spec/SpecPage.tsx`).
- `dashboard/src/hooks.ts` — `useApi(path, deps)`.

Modified:
- `dashboard/src/router.ts` — new hash scheme `#/p/:projectId`, redirects from the old hashes.
- `dashboard/src/api/routes.ts` — new endpoint paths.
- `dashboard/src/app.tsx` — single-page shell.
- `dashboard/src/shell/Sidebar.tsx`, `TabBar.tsx`, `TopBar.tsx`, `ProjectMenu.tsx` — project switcher only, Agents/History entries.
- `dashboard/src/node/NodeSheet.tsx`, `Actions.tsx` — new sections and actions (remove, edit, break down).
- `src/muvue/api/app.py` — `GET /projects/{id}/revisions` stays as-is; no other backend change is needed (confirmed in Task 1's research step below).

Deleted: `dashboard/src/plan/`, `dashboard/src/inbox/`, `dashboard/src/activity/`, `dashboard/src/spend/`, `dashboard/src/spec/`, and their tests (superseded by the new modules' own tests, written first).

---

### Task 1: Canvas data model (`canvasData.ts`)

**Files:**
- Create: `dashboard/src/canvas/canvasData.ts`
- Test: `dashboard/test/canvasData.test.ts`

**Interfaces:**
- Consumes: `GET /graph?project_id=` response `{ nodes: GraphNode[], edges: GraphEdge[] }` where `GraphNode = { id, project_id, parent_id, kind, title, status, risk_tier, owner, agent }` and `GraphEdge = { from, to, kind: "parent" | "dep", carries?: string | null }` (confirmed against `src/muvue/api/app.py`'s `graph()` handler); `GET /nodes?project_id=` response: full node rows (`SELECT *`), i.e. `NodeRow & { body_md, criteria_json, criteria_hash, criteria_mode, summary, block_reason, worktree, deleted_at, ... }`.
- Produces: `CanvasSpec`, `CanvasSubtask`, `CanvasTask`, `CanvasEdge`, `CanvasData`, `buildCanvasData(graph, fullNodes)` — used by Task 2 (`flowLayout`), Task 6 (`TaskBox`), Task 7 (`SpecRoot`), Task 8 (`Canvas`), Task 13 (`ProjectPage`).

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/test/canvasData.test.ts
import { buildCanvasData } from "../src/canvas/canvasData";

const gnode = (id: number, kind: string, parent_id: number | null, extra: Partial<any> = {}) => ({
  id, project_id: 1, parent_id, kind, title: "g" + id, status: "ready", risk_tier: "low", owner: null, agent: "claude", ...extra,
});
const fnode = (id: number, kind: string, parent_id: number | null, extra: Partial<any> = {}) => ({
  id, project_id: 1, parent_id, kind, title: "n" + id, status: "ready", risk_tier: "low", owner: null,
  body_md: "purpose " + id, criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null, ...extra,
});

test("builds one spec, tasks with nested subtasks, and dep edges restricted to tasks", () => {
  const graph = {
    nodes: [gnode(1, "spec", null), gnode(2, "task", 1), gnode(3, "task", 1), gnode(4, "subtask", 2)],
    edges: [
      { from: 1, to: 2, kind: "parent" as const },
      { from: 1, to: 3, kind: "parent" as const },
      { from: 2, to: 4, kind: "parent" as const },
      { from: 2, to: 3, kind: "dep" as const, carries: "audio frames" },
    ],
  };
  const nodes = [fnode(1, "spec", null), fnode(2, "task", 1), fnode(3, "task", 1), fnode(4, "subtask", 2, { title: "sub" })];
  const data = buildCanvasData(graph, nodes);
  expect(data.spec?.id).toBe(1);
  expect(data.spec?.body_md).toBe("purpose 1");
  expect(data.tasks.map((t) => t.id)).toEqual([2, 3]);
  expect(data.tasks[0]!.subtasks.map((s) => s.id)).toEqual([4]);
  expect(data.tasks[0]!.subtasks[0]!.title).toBe("sub");
  expect(data.tasks[0]!.agent).toBe("claude");
  expect(data.edges).toEqual([{ from: 2, to: 3, carries: "audio frames" }]);
});

test("with no spec node, spec is null and tasks are still built", () => {
  const graph = { nodes: [gnode(2, "task", null)], edges: [] };
  const data = buildCanvasData(graph, [fnode(2, "task", null)]);
  expect(data.spec).toBeNull();
  expect(data.tasks.map((t) => t.id)).toEqual([2]);
});

test("a dep edge whose endpoint was deleted (absent from fullNodes) is dropped, not thrown", () => {
  const graph = { nodes: [gnode(2, "task", null)], edges: [{ from: 2, to: 99, kind: "dep" as const, carries: null }] };
  const data = buildCanvasData(graph, [fnode(2, "task", null)]);
  expect(data.edges).toEqual([]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/canvasData.test.ts`
Expected: FAIL — `Cannot find module '../src/canvas/canvasData'`

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/canvas/canvasData.ts
import type { NodeRow, NodeStatus, RiskTier } from "../state";

export type GraphNode = NodeRow & { agent: string | null };
export type GraphEdge = { from: number; to: number; kind: "parent" | "dep"; carries?: string | null };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type FullNode = NodeRow & {
  body_md: string | null; criteria_json: string; criteria_hash: string | null; block_reason: string | null; deleted_at: string | null;
};

export type CanvasSubtask = { id: number; title: string; status: NodeStatus; parent_id: number };
export type CanvasSpec = { id: number; title: string; body_md: string | null; status: NodeStatus; agent: string | null };
export type CanvasTask = {
  id: number; title: string; status: NodeStatus; risk_tier: RiskTier; owner: string | null; agent: string | null;
  body_md: string | null; criteria_hash: string | null; block_reason: string | null; subtasks: CanvasSubtask[];
};
export type CanvasEdge = { from: number; to: number; carries: string | null };
export type CanvasData = { spec: CanvasSpec | null; tasks: CanvasTask[]; edges: CanvasEdge[] };

// The purpose line: the first non-empty line of body_md, per "The canvas ->
// Task box" (this belongs on canvasData, not the component, since AddForm's
// ghost box needs the same rule before any body_md round-trips through the API).
export function purposeLine(bodyMd: string | null): string {
  return (bodyMd || "").split("\n").find((l) => l.trim())?.trim() ?? "";
}

export function buildCanvasData(graph: Graph, fullNodes: FullNode[]): CanvasData {
  const agentById = new Map(graph.nodes.map((n) => [n.id, n.agent]));
  const byId = new Map(fullNodes.map((n) => [n.id, n]));
  const specRow = fullNodes.find((n) => n.kind === "spec") ?? null;
  const spec: CanvasSpec | null = specRow
    ? { id: specRow.id, title: specRow.title, body_md: specRow.body_md, status: specRow.status, agent: agentById.get(specRow.id) ?? null }
    : null;
  const subtasksByParent = new Map<number, CanvasSubtask[]>();
  for (const n of fullNodes) {
    if (n.kind !== "subtask" || n.parent_id === null) continue;
    const list = subtasksByParent.get(n.parent_id) ?? [];
    list.push({ id: n.id, title: n.title, status: n.status, parent_id: n.parent_id });
    subtasksByParent.set(n.parent_id, list);
  }
  const tasks: CanvasTask[] = fullNodes
    .filter((n) => n.kind === "task")
    .map((n) => ({
      id: n.id, title: n.title, status: n.status, risk_tier: n.risk_tier, owner: n.owner,
      agent: agentById.get(n.id) ?? null, body_md: n.body_md, criteria_hash: n.criteria_hash,
      block_reason: n.block_reason, subtasks: (subtasksByParent.get(n.id) ?? []).sort((a, b) => a.id - b.id),
    }));
  const taskIds = new Set(tasks.map((t) => t.id));
  const edges: CanvasEdge[] = graph.edges
    .filter((e) => e.kind === "dep" && taskIds.has(e.from) && taskIds.has(e.to) && byId.has(e.from) && byId.has(e.to))
    .map((e) => ({ from: e.from, to: e.to, carries: e.carries ?? null }));
  return { spec, tasks, edges };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/canvasData.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/canvasData.ts test/canvasData.test.ts
git commit -m "Canvas: pure data model merging /graph and /nodes"
```

---

### Task 2: Flow layout (`flowLayout.ts`)

**Files:**
- Create: `dashboard/src/canvas/flowLayout.ts`
- Test: `dashboard/test/flowLayout.test.ts`

**Interfaces:**
- Consumes: `CanvasTask`, `CanvasEdge` (Task 1).
- Produces: `BOX_W`, `PAD`, `GAP_X`, `GAP_Y`, `SUBTASK_ROW_H`, `BOX_H_BASE`, `computeLayout(tasks, edges) -> Layout`, `stepsFromColumns(tasks, edges) -> Step[]` — consumed by Task 6 (box height), Task 8 (`Canvas`), Task 9 (`PhoneFlow`).

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/test/flowLayout.test.ts
import { computeLayout, stepsFromColumns, boxHeight, BOX_W, GAP_X } from "../src/canvas/flowLayout";
import type { CanvasTask, CanvasEdge } from "../src/canvas/canvasData";

const task = (id: number, subtaskCount = 0): CanvasTask => ({
  id, title: "t" + id, status: "ready", risk_tier: "low", owner: null, agent: "claude",
  body_md: null, criteria_hash: null, block_reason: null,
  subtasks: Array.from({ length: subtaskCount }, (_, i) => ({ id: id * 100 + i, title: "s" + i, status: "ready" as const, parent_id: id })),
});

test("a task with no deps sits in column 0; a dependent sits one column later", () => {
  const tasks = [task(1), task(2)];
  const edges: CanvasEdge[] = [{ from: 1, to: 2, carries: null }];
  const lay = computeLayout(tasks, edges);
  expect(lay.pos[1]!.col).toBe(0);
  expect(lay.pos[2]!.col).toBe(1);
  expect(lay.pos[2]!.x).toBeGreaterThan(lay.pos[1]!.x + BOX_W);
});

test("box height grows with subtask count, capped at 4 rows plus a +N more row", () => {
  expect(boxHeight(0)).toBeLessThan(boxHeight(2));
  expect(boxHeight(4)).toBeLessThan(boxHeight(10));
  // a 5th+ subtask adds exactly one more row (the "+N more" row), not one per extra subtask
  expect(boxHeight(5)).toBe(boxHeight(10));
});

test("within a column, order follows mean predecessor row, then id", () => {
  const tasks = [task(1), task(2), task(3), task(4)];
  // 3 depends on 1 (row 0), 4 depends on 2 (row 1) -> in column 1, 3 should sit above 4
  const edges: CanvasEdge[] = [{ from: 1, to: 3, carries: null }, { from: 2, to: 4, carries: null }];
  const lay = computeLayout(tasks, edges);
  expect(lay.pos[3]!.y).toBeLessThan(lay.pos[4]!.y);
});

test("a dep cycle does not hang layout and both nodes land in column 0", () => {
  const tasks = [task(1), task(2)];
  const edges: CanvasEdge[] = [{ from: 1, to: 2, carries: null }, { from: 2, to: 1, carries: null }];
  const lay = computeLayout(tasks, edges);
  expect(lay.pos[1]!.col).toBe(0);
  expect(lay.pos[2]!.col).toBe(0);
});

test("an arrow has an orthogonal path and a label point only when carries is set", () => {
  const tasks = [task(1), task(2), task(3)];
  const edges: CanvasEdge[] = [{ from: 1, to: 2, carries: "audio frames" }, { from: 1, to: 3, carries: null }];
  const lay = computeLayout(tasks, edges);
  const withLabel = lay.arrows.find((a) => a.from === 1 && a.to === 2)!;
  const noLabel = lay.arrows.find((a) => a.from === 1 && a.to === 3)!;
  expect(withLabel.path.split(" ").length).toBeGreaterThanOrEqual(3); // M, L, L: at least two segments
  expect(withLabel.label?.text).toBe("audio frames");
  expect(noLabel.label).toBeNull();
});

test("stepsFromColumns groups same-column tasks as one parallel step", () => {
  const tasks = [task(1), task(2), task(3)];
  const edges: CanvasEdge[] = [{ from: 1, to: 3, carries: null }, { from: 2, to: 3, carries: null }];
  const steps = stepsFromColumns(tasks, edges);
  expect(steps[0]!.tasks.map((t) => t.id).sort()).toEqual([1, 2]);
  expect(steps[0]!.parallel).toBe(true);
  expect(steps[1]!.tasks.map((t) => t.id)).toEqual([3]);
  expect(steps[1]!.parallel).toBe(false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/flowLayout.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/canvas/flowLayout.ts
import type { CanvasTask, CanvasEdge } from "./canvasData";

export const BOX_W = 240, PAD = 16, GAP_X = 72, GAP_Y = 24, SUBTASK_ROW_H = 22, BOX_H_BASE = 64, MAX_SUBTASK_ROWS = 4;

export function boxHeight(subtaskCount: number): number {
  const rows = subtaskCount === 0 ? 0 : Math.min(subtaskCount, MAX_SUBTASK_ROWS) + (subtaskCount > MAX_SUBTASK_ROWS ? 1 : 0);
  return BOX_H_BASE + rows * SUBTASK_ROW_H;
}

type Placed = { x: number; y: number; w: number; h: number; col: number; row: number };
type Arrow = { from: number; to: number; path: string; label: { x: number; y: number; text: string } | null };
export type Layout = { pos: Record<number, Placed>; arrows: Arrow[]; width: number; height: number };
export type Step = { col: number; tasks: CanvasTask[]; parallel: boolean };

function columnsOf(tasks: CanvasTask[], edges: CanvasEdge[]): Record<number, number> {
  const incoming: Record<number, number[]> = {};
  for (const t of tasks) incoming[t.id] = [];
  for (const e of edges) incoming[e.to]?.push(e.from);
  const col: Record<number, number> = {};
  function colOf(id: number, seen: Set<number>): number {
    if (col[id] !== undefined) return col[id]!;
    if (seen.has(id)) return 0; // a cycle is a data bug; don't hang on it
    seen.add(id);
    let c = 0;
    for (const from of incoming[id] ?? []) c = Math.max(c, colOf(from, seen) + 1);
    col[id] = c;
    return c;
  }
  for (const t of tasks) colOf(t.id, new Set());
  return col;
}

export function computeLayout(tasks: CanvasTask[], edges: CanvasEdge[]): Layout {
  const col = columnsOf(tasks, edges);
  const incoming: Record<number, number[]> = {};
  for (const t of tasks) incoming[t.id] = [];
  for (const e of edges) incoming[e.to]?.push(e.from);
  const columns: number[][] = [];
  for (const t of tasks) (columns[col[t.id]!] ??= []).push(t.id);
  const rowOf: Record<number, number> = {};
  const pos: Record<number, Placed> = {};
  const byId = new Map(tasks.map((t) => [t.id, t]));
  columns.forEach((ids, c) => {
    const mean = (id: number) => {
      const rows = (incoming[id] ?? []).filter((f) => rowOf[f] !== undefined).map((f) => rowOf[f]!);
      return rows.length ? rows.reduce((s, r) => s + r, 0) / rows.length : id;
    };
    ids.sort((a, b) => mean(a) - mean(b) || a - b);
    let y = PAD;
    ids.forEach((id, i) => {
      rowOf[id] = i;
      const h = boxHeight(byId.get(id)!.subtasks.length);
      pos[id] = { x: PAD + c * (BOX_W + GAP_X), y, w: BOX_W, h, col: c, row: i };
      y += h + GAP_Y;
    });
  });
  const width = PAD * 2 + columns.length * BOX_W + Math.max(0, columns.length - 1) * GAP_X;
  const height = PAD * 2 + Math.max(0, ...Object.values(pos).map((p) => p.y + p.h));
  const arrows: Arrow[] = edges.map((e) => {
    const a = pos[e.from]!, b = pos[e.to]!;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    const midX = x1 + GAP_X / 2;
    const path = `M${x1},${y1} L${midX},${y1} L${midX},${y2} L${x2},${y2}`;
    const label = e.carries ? { x: midX, y: (y1 + y2) / 2, text: e.carries } : null;
    return { from: e.from, to: e.to, path, label };
  });
  return { pos, arrows, width, height };
}

export function stepsFromColumns(tasks: CanvasTask[], edges: CanvasEdge[]): Step[] {
  const col = columnsOf(tasks, edges);
  const byCol = new Map<number, CanvasTask[]>();
  for (const t of tasks) { const list = byCol.get(col[t.id]!) ?? []; list.push(t); byCol.set(col[t.id]!, list); }
  return [...byCol.entries()].sort(([a], [b]) => a - b).map(([c, ts]) => ({
    col: c, tasks: [...ts].sort((a, b) => a.id - b.id), parallel: ts.length > 1,
  }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/flowLayout.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/flowLayout.ts test/flowLayout.test.ts
git commit -m "Canvas: pure flow layout — columns, box sizes, orthogonal arrows"
```

---

### Task 3: Pending registry (`pending.ts`)

**Files:**
- Create: `dashboard/src/canvas/pending.ts`
- Test: `dashboard/test/pending.test.ts`

**Interfaces:**
- Consumes: nothing (pure state module).
- Produces: `pendingCreates` signal, `pendingBreakdowns` signal, `startCreate`, `resolveCreate`, `failCreate`, `startBreakdown`, `shouldClearBreakdown` — consumed by Task 7 (`GhostBox`), Task 10 (`AddForm`), Task 13 (`ProjectPage`, clears breakdown ghosts on refresh).

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/test/pending.test.ts
import { pendingCreates, pendingBreakdowns, startCreate, resolveCreate, failCreate, startBreakdown, shouldClearBreakdown } from "../src/canvas/pending";

test("startCreate then resolveCreate clears the ghost", () => {
  startCreate("task:1:new", { kind: "task", parentId: 1, title: "Do the thing" });
  expect(pendingCreates.value["task:1:new"]!.title).toBe("Do the thing");
  resolveCreate("task:1:new");
  expect(pendingCreates.value["task:1:new"]).toBeUndefined();
});

test("failCreate keeps the ghost with an error for Retry", () => {
  startCreate("task:1:new", { kind: "task", parentId: 1, title: "x" });
  failCreate("task:1:new", "network error");
  expect(pendingCreates.value["task:1:new"]!.error).toBe("network error");
});

test("shouldClearBreakdown is true once more children exist than at breakdown start", () => {
  startBreakdown(5, { nodeId: 5, agent: "claude", log: ".muvue/logs/breakdown-5.log", startChildIds: new Set([10, 11]) });
  expect(pendingBreakdowns.value[5]).toBeDefined();
  expect(shouldClearBreakdown(pendingBreakdowns.value[5]!, new Set([10, 11]))).toBe(false);
  expect(shouldClearBreakdown(pendingBreakdowns.value[5]!, new Set([10, 11, 12, 13, 14]))).toBe(true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/pending.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/canvas/pending.ts
import { signal } from "@preact/signals";

export type PendingCreate = { kind: "task" | "subtask"; parentId: number; title: string; error?: string };
export type PendingBreakdown = { nodeId: number; agent: string; log: string; startChildIds: Set<number> };

export const pendingCreates = signal<Record<string, PendingCreate>>({});
export const pendingBreakdowns = signal<Record<number, PendingBreakdown>>({});

export function startCreate(key: string, p: PendingCreate): void {
  pendingCreates.value = { ...pendingCreates.value, [key]: p };
}
export function resolveCreate(key: string): void {
  const { [key]: _removed, ...rest } = pendingCreates.value;
  pendingCreates.value = rest;
}
export function failCreate(key: string, error: string): void {
  const existing = pendingCreates.value[key];
  if (existing) pendingCreates.value = { ...pendingCreates.value, [key]: { ...existing, error } };
}

export function startBreakdown(nodeId: number, p: PendingBreakdown): void {
  pendingBreakdowns.value = { ...pendingBreakdowns.value, [nodeId]: p };
}
export function clearBreakdown(nodeId: number): void {
  const { [nodeId]: _removed, ...rest } = pendingBreakdowns.value;
  pendingBreakdowns.value = rest;
}
// A breakdown is done, for ghost-clearing purposes, once the node's live
// children outnumber the set present when the breakdown was launched.
// (`breakdown.finished`'s own event isn't polled directly here — the
// canvas already refetches on every SSE tick, so comparing child-id sets
// on that same tick is simpler than a second data source.)
export function shouldClearBreakdown(p: PendingBreakdown, currentChildIds: Set<number>): boolean {
  return currentChildIds.size > p.startChildIds.size;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/pending.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/pending.ts test/pending.test.ts
git commit -m "Canvas: pending-request registry for ghosts and spinners"
```

---

### Task 4: Routes, router and `useApi`

**Files:**
- Modify: `dashboard/src/api/routes.ts`
- Modify: `dashboard/src/router.ts`
- Create: `dashboard/src/hooks.ts`
- Test: `dashboard/test/router.test.ts` (extend existing), `dashboard/test/routes.test.ts`, `dashboard/test/hooks.test.tsx`

**Interfaces:**
- Produces: `routes.nodeChildren(id)`, `routes.nodeRemove(id)`, `routes.nodeEdit(id)`, `routes.nodeBreakdown(id)`, `routes.projectRun(id)`, `routes.agentsStatus(projectId)`, `routes.nodeRuns(id)`; `parseHash` recognizing `#/p/:id`; `openProject(id)`, `redirectLegacyHash()`; `useApi<T>(path, deps) -> { data, error, reload }` — consumed by every later task's data fetching.

- [ ] **Step 1: Write the failing tests**

```typescript
// dashboard/test/routes.test.ts
import { routes } from "../src/api/routes";

test("new canvas-backend routes are spelled correctly", () => {
  expect(routes.nodeChildren(7)).toBe("/nodes/7/children");
  expect(routes.nodeRemove(7)).toBe("/nodes/7/remove");
  expect(routes.nodeEdit(7)).toBe("/nodes/7/edit");
  expect(routes.nodeBreakdown(7)).toBe("/nodes/7/breakdown");
  expect(routes.projectRun(3)).toBe("/projects/3/run");
  expect(routes.agentsStatus(3)).toBe("/agents/status?project_id=3");
  expect(routes.nodeRuns(7)).toBe("/nodes/7/runs");
});
```

```typescript
// dashboard/test/router.test.ts (append to the existing file)
import { parseHash } from "../src/router";

test("#/p/:id parses to page 'p' with the project id as the first param", () => {
  const r = parseHash("#/p/3");
  expect(r.page).toBe("p");
  expect(r.params).toEqual(["3"]);
});

test("#/p/3?node=9 carries the node query param", () => {
  const r = parseHash("#/p/3?node=9");
  expect(r.query.get("node")).toBe("9");
});
```

```typescript
// dashboard/test/hooks.test.tsx
import { render, screen, waitFor } from "@testing-library/preact";
import { useApi } from "../src/hooks";

test("useApi resolves data and re-fetches when deps change, ignoring a stale response", async () => {
  let calls = 0;
  const fetcher = (id: number) => new Promise<{ id: number }>((resolve) => {
    calls++;
    // the first call resolves slower than the second, to prove the stale
    // response for id=1 never overwrites the fresh one for id=2
    setTimeout(() => resolve({ id }), id === 1 ? 20 : 0);
  });
  function Probe({ id }: { id: number }) {
    const { data, error } = useApi(() => fetcher(id), [id]);
    return <div>{error ? "error" : data ? `id:${data.id}` : "loading"}</div>;
  }
  const { rerender } = render(<Probe id={1} />);
  rerender(<Probe id={2} />);
  await waitFor(() => screen.getByText("id:2"));
  await new Promise((r) => setTimeout(r, 30));
  expect(screen.queryByText("id:1")).toBeNull();
  expect(calls).toBe(2);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npx vitest run test/routes.test.ts test/router.test.ts test/hooks.test.tsx`
Expected: FAIL — new route keys and `../src/hooks` don't exist yet; the two new router assertions fail against the old hash scheme

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/api/routes.ts — add these entries to the existing `routes` object, and keep everything else unchanged
  nodeChildren: (id: number) => `/nodes/${id}/children`,
  nodeRemove: (id: number) => `/nodes/${id}/remove`,
  nodeEdit: (id: number) => `/nodes/${id}/edit`,
  nodeBreakdown: (id: number) => `/nodes/${id}/breakdown`,
  projectRun: (id: number) => `/projects/${id}/run`,
  agentsStatus: (projectId: number) => q("/agents/status", { project_id: projectId }),
  nodeRuns: (id: number) => `/nodes/${id}/runs`,
```

```typescript
// dashboard/src/router.ts — replace the whole file
import { signal } from "@preact/signals";

export type Route = { page: string; params: string[]; query: URLSearchParams };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart = "", queryPart = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  if (!parts.length || parts[0]!.startsWith("n=")) return { page: "", params: [], query: new URLSearchParams(queryPart) };
  return { page: parts[0]!, params: parts.slice(1), query: new URLSearchParams(queryPart) };
}

export const route = signal<Route>(parseHash(typeof window === "undefined" ? "" : window.location.hash));

export function resetRouteFromLocation(): void { route.value = parseHash(window.location.hash); }

if (typeof window !== "undefined") window.addEventListener("hashchange", resetRouteFromLocation);

export function navigate(hash: string): void { window.location.hash = hash; }

export function openProject(id: number): void { navigate("#/p/" + id); }

export function openNode(id: number): void {
  const r = route.value;
  const q = new URLSearchParams(r.query);
  q.set("node", String(id));
  navigate("#/" + [r.page, ...r.params].join("/") + "?" + q.toString());
}

export function closeNode(): void {
  const r = route.value;
  const q = new URLSearchParams(r.query);
  q.delete("node");
  const qs = q.toString();
  navigate("#/" + [r.page, ...r.params].join("/") + (qs ? "?" + qs : ""));
}

// The tab pages this replaces all folded into one project canvas; a link
// to any of them (bookmarked, or from history) lands on that project's
// canvas instead of a 404. "#/spec/:id" is the one case that names a
// node rather than a project — the caller resolves its project first.
const LEGACY_PAGES = new Set(["plan", "inbox", "activity", "spend"]);
export function isLegacyRoute(r: Route): boolean { return LEGACY_PAGES.has(r.page) || r.page === "spec"; }
```

```typescript
// dashboard/src/hooks.ts
import { useEffect, useState } from "preact/hooks";

// Every canvas/card/node view had its own copy of this alive-guarded
// fetch-on-deps-change effect (final review flagged the duplication in
// the dashboard-redesign plan). `fetcher` is a thunk, not a path, so a
// caller can combine two endpoints (canvasData does) or add headers.
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    fetcher().then(
      (d) => { if (alive) { setData(d); setError(null); } },
      (e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npx vitest run test/routes.test.ts test/router.test.ts test/hooks.test.tsx`
Expected: PASS. If `@testing-library/preact` is not yet a dev dependency, run `cd dashboard && npm install -D @testing-library/preact` first (needed only for `hooks.test.tsx`; every other test in this plan uses plain Vitest assertions on pure functions).

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/api/routes.ts src/router.ts src/hooks.ts test/routes.test.ts test/router.test.ts test/hooks.test.tsx package.json package-lock.json
git commit -m "Dashboard: canvas backend routes, #/p/:id router, useApi hook"
```

---

### Task 5: Agent state and `AgentChip`

**Files:**
- Create: `dashboard/src/canvas/agentState.ts`, `dashboard/src/canvas/AgentChip.tsx`
- Test: `dashboard/test/agentState.test.ts`

**Interfaces:**
- Consumes: `CanvasTask`/`CanvasSpec`-shaped `{ status, owner, agent }`, plus the project's `phase` (for the `paused` state) and the node's open-question/review flag (for `waiting on you`).
- Produces: `AgentState = "queued" | "running" | "waiting_on_you" | "done" | "failed" | "paused" | "unassigned"`, `agentStateOf(node, projectPhase, needsYou) -> AgentState`, `<AgentChip agent={string|null} state={AgentState} />` — consumed by Task 6 (`TaskBox`), Task 7 (`SpecRoot`).

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/test/agentState.test.ts
import { agentStateOf } from "../src/canvas/agentState";

const node = (status: string, owner: string | null = null) => ({ status, owner, agent: "claude" } as const);

test("every state per the design's Agent chip states", () => {
  expect(agentStateOf(node("ready"), "planning", false)).toBe("queued");
  expect(agentStateOf(node("in_progress", "claude"), "planning", false)).toBe("running");
  expect(agentStateOf(node("review"), "planning", true)).toBe("waiting_on_you");
  expect(agentStateOf(node("awaiting_approval"), "planning", true)).toBe("waiting_on_you");
  expect(agentStateOf(node("done"), "planning", false)).toBe("done");
  expect(agentStateOf(node("failed"), "planning", false)).toBe("failed");
  expect(agentStateOf(node("in_progress"), "paused", false)).toBe("paused");
});

test("no routed or owning agent is unassigned regardless of status", () => {
  expect(agentStateOf({ status: "ready", owner: null, agent: null }, "planning", false)).toBe("unassigned");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/agentState.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/canvas/agentState.ts
export type AgentState = "queued" | "running" | "waiting_on_you" | "done" | "failed" | "paused" | "unassigned";

export function agentStateOf(
  node: { status: string; owner: string | null; agent: string | null },
  projectPhase: string,
  needsYou: boolean,
): AgentState {
  if (!node.agent) return "unassigned";
  if (projectPhase === "paused" && (node.status === "in_progress" || node.status === "ready")) return "paused";
  if (node.status === "done") return "done";
  if (node.status === "failed") return "failed";
  if (needsYou || node.status === "review" || node.status === "awaiting_approval") return "waiting_on_you";
  if (node.status === "in_progress") return "running";
  return "queued";
}
```

```typescript
// dashboard/src/canvas/AgentChip.tsx
import type { AgentState } from "./agentState";

const LABEL: Record<AgentState, string> = {
  queued: "queued", running: "running", waiting_on_you: "waiting on you", done: "done", failed: "failed",
  paused: "paused", unassigned: "unassigned",
};

export function AgentChip({ agent, state }: { agent: string | null; state: AgentState }) {
  return (
    <span class={"chip agent-chip st-" + state}>
      {state === "running" ? <span class="pulse" aria-hidden="true" /> : null}
      {agent ? `${agent} · ${LABEL[state]}` : LABEL[state]}
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/agentState.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/agentState.ts src/canvas/AgentChip.tsx test/agentState.test.ts
git commit -m "Canvas: agent state derivation and the AgentChip"
```

---

### Task 6: `TaskBox` and `SubtaskRow`

**Files:**
- Create: `dashboard/src/canvas/SubtaskRow.tsx`, `dashboard/src/canvas/TaskBox.tsx`
- Test: `dashboard/test/TaskBox.test.tsx`

**Interfaces:**
- Consumes: `CanvasTask` (Task 1), `purposeLine` (Task 1), `AgentChip`/`agentStateOf` (Task 5), `boxHeight` (Task 2), `openNode` (Task 4).
- Produces: `<TaskBox task={CanvasTask} needsYou={Set<number>} projectPhase={string} style={...} />`, `<SubtaskRow subtask={CanvasSubtask} needsYou={boolean} />` — consumed by Task 8 (`Canvas`), Task 9 (`PhoneFlow`).

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/test/TaskBox.test.tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { TaskBox } from "../src/canvas/TaskBox";
import type { CanvasTask } from "../src/canvas/canvasData";

const task: CanvasTask = {
  id: 5, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude",
  body_md: "turn audio into notes\nmore detail", criteria_hash: null, block_reason: null,
  subtasks: [
    { id: 51, title: "YIN tracker", status: "done", parent_id: 5 },
    { id: 52, title: "smoothing", status: "ready", parent_id: 5 },
  ],
};

test("shows title, purpose line, subtask rows, and the agent chip", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Detect pitch")).toBeTruthy();
  expect(screen.getByText("turn audio into notes")).toBeTruthy();
  expect(screen.getByText("YIN tracker")).toBeTruthy();
  expect(screen.getByText(/claude/)).toBeTruthy();
});

test("more than 4 subtasks show a +N more row instead of all of them", () => {
  const many: CanvasTask = { ...task, subtasks: Array.from({ length: 6 }, (_, i) => ({ id: 60 + i, title: "s" + i, status: "ready" as const, parent_id: 5 })) };
  render(<TaskBox task={many} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("+2 more")).toBeTruthy();
});

test("a needs-you badge appears when the task id is in the needsYou set, and opens the node on click", () => {
  const { container } = render(<TaskBox task={task} needsYou={new Set([5])} projectPhase="executing" />);
  expect(container.querySelector(".needs-you-dot")).toBeTruthy();
  fireEvent.click(screen.getByText("Detect pitch"));
  expect(window.location.hash).toContain("node=5");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/TaskBox.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/canvas/SubtaskRow.tsx
import type { CanvasSubtask } from "./canvasData";
import { openNode } from "../router";

const GLYPH: Record<string, string> = { done: "✓", in_progress: "●", review: "●", pending: "○", ready: "○", blocked: "⚠", failed: "⚠", awaiting_approval: "●" };

export function SubtaskRow({ subtask, needsYou }: { subtask: CanvasSubtask; needsYou: boolean }) {
  return (
    <button type="button" class="subtask-row" onClick={(e) => { e.stopPropagation(); openNode(subtask.id); }}>
      <span class="glyph" style={{ color: `var(--st-${subtask.status})` }}>{GLYPH[subtask.status] ?? "○"}</span>
      <span class="grow title">{subtask.title}</span>
      {needsYou ? <span class="needs-you-dot" aria-label="needs you" /> : null}
    </button>
  );
}
```

```tsx
// dashboard/src/canvas/TaskBox.tsx
import type { CanvasTask } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";
import { SubtaskRow } from "./SubtaskRow";
import { MAX_SUBTASK_ROWS } from "./flowLayout";

export function TaskBox({ task, needsYou, projectPhase, style }: { task: CanvasTask; needsYou: Set<number>; projectPhase: string; style?: Record<string, string | number> }) {
  const shown = task.subtasks.slice(0, MAX_SUBTASK_ROWS);
  const more = task.subtasks.length - shown.length;
  const state = agentStateOf(task, projectPhase, needsYou.has(task.id));
  return (
    <div class={"task-box st-bar-" + task.status} style={style} tabIndex={0} onClick={() => openNode(task.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(task.id); }}>
      <div class="row between">
        <span class="title grow">{task.title}</span>
        {needsYou.has(task.id) ? <span class="needs-you-dot" aria-label="needs you" /> : null}
      </div>
      {task.body_md ? <div class="caption purpose">{purposeLine(task.body_md)}</div> : null}
      {shown.length ? (
        <div class="subtask-list">
          {shown.map((s) => <SubtaskRow subtask={s} needsYou={needsYou.has(s.id)} />)}
          {more > 0 ? <div class="caption more-row">+{more} more</div> : null}
        </div>
      ) : null}
      <AgentChip agent={task.agent} state={state} />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/TaskBox.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/SubtaskRow.tsx src/canvas/TaskBox.tsx test/TaskBox.test.tsx
git commit -m "Canvas: TaskBox and SubtaskRow"
```

---

### Task 7: `SpecRoot` and `GhostBox`

**Files:**
- Create: `dashboard/src/canvas/SpecRoot.tsx`, `dashboard/src/canvas/GhostBox.tsx`
- Test: `dashboard/test/SpecRoot.test.tsx`, `dashboard/test/GhostBox.test.tsx`

**Interfaces:**
- Consumes: `CanvasSpec`, `post`/`routes` (spec approve, spec submit), `PendingCreate`/`PendingBreakdown` (Task 3).
- Produces: `<SpecRoot spec={CanvasSpec|null} projectId={number} taskCount={number} projectPhase={string} />`, `<GhostBox title={string} error={string|undefined} caption={string} onRetry={()=>void} />` — consumed by Task 8/9/13.

- [ ] **Step 1: Write the failing tests**

```tsx
// dashboard/test/SpecRoot.test.tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { SpecRoot } from "../src/canvas/SpecRoot";

test("no spec yet shows the inline title/body submit form", () => {
  render(<SpecRoot spec={null} projectId={1} taskCount={0} projectPhase="planning" />);
  expect(screen.getByPlaceholderText("title")).toBeTruthy();
  expect(screen.getByText("Submit spec")).toBeTruthy();
});

test("spec pending shows the spec title and an Approve spec button", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "pending", agent: null }} projectId={1} taskCount={0} projectPhase="planning" />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("Approve spec")).toBeTruthy();
});

test("spec approved with tasks and phase planning shows Approve task list", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "x", status: "ready", agent: null }} projectId={1} taskCount={3} projectPhase="planning" />);
  expect(screen.getByText("Approve task list")).toBeTruthy();
});
```

```tsx
// dashboard/test/GhostBox.test.tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { GhostBox } from "../src/canvas/GhostBox";

test("shows the typed title while pending, and a caption", () => {
  render(<GhostBox title="Export MusicXML" caption="creating…" />);
  expect(screen.getByText("Export MusicXML")).toBeTruthy();
  expect(screen.getByText("creating…")).toBeTruthy();
});

test("an error shows the message and a Retry button", () => {
  const onRetry = () => { onRetry.called = true; };
  onRetry.called = false;
  render(<GhostBox title="Export MusicXML" caption="creating…" error="network error" onRetry={onRetry} />);
  expect(screen.getByText("network error")).toBeTruthy();
  fireEvent.click(screen.getByText("Retry"));
  expect(onRetry.called).toBe(true);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npx vitest run test/SpecRoot.test.tsx test/GhostBox.test.tsx`
Expected: FAIL — modules not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/canvas/GhostBox.tsx
import { Button } from "../ui/Button";

export function GhostBox({ title, caption, error, onRetry, logLink }: { title: string; caption: string; error?: string; onRetry?: () => void; logLink?: string }) {
  return (
    <div class={"task-box ghost" + (error ? " ghost-error" : "")}>
      <div class="title">{title}</div>
      {error ? (
        <div class="stack tight">
          <div class="caption danger">{error}</div>
          {onRetry ? <Button variant="plain" onClick={onRetry}>Retry</Button> : null}
        </div>
      ) : (
        <div class="row">
          <span class="pulse" aria-hidden="true" />
          <span class="caption">{caption}</span>
          {logLink ? <a href={logLink} class="caption">view log</a> : null}
        </div>
      )}
    </div>
  );
}
```

```tsx
// dashboard/src/canvas/SpecRoot.tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import type { CanvasSpec } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";

function SubmitSpecForm({ projectId }: { projectId: number }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: Event) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    try { await post(routes.projectSpec(projectId), { title, body_md: body }); toast("spec submitted"); refresh(); } catch (e) { toastError(e); } finally { setBusy(false); }
  }
  return (
    <form class="spec-root-card stack" onSubmit={submit}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one requirement per line" value={body} onInput={(e) => setBody((e.target as HTMLTextAreaElement).value)} />
      <div class="actions"><Button type="submit" variant="filled" disabled={busy}>Submit spec</Button></div>
    </form>
  );
}

export function SpecRoot({ spec, projectId, taskCount, projectPhase }: { spec: CanvasSpec | null; projectId: number; taskCount: number; projectPhase: string }) {
  if (!spec) return authed.value ? <SubmitSpecForm projectId={projectId} /> : <div class="spec-root-card"><p class="muted">No spec yet.</p></div>;
  const canApproveSpec = authed.value && spec.status === "pending";
  const canApproveTasks = authed.value && spec.status !== "pending" && taskCount > 0 && projectPhase === "planning";
  const state = agentStateOf({ status: spec.status, owner: null, agent: spec.agent }, projectPhase, false);

  async function approveSpec() { try { await post(routes.nodeApprove(spec!.id), { target: "spec" }); toast("spec approved"); refresh(); } catch (e) { toastError(e); } }
  async function approveTasks() { try { await post(routes.nodeApprove(projectId), { target: "gate2" }); toast("task list approved; criteria frozen"); refresh(); } catch (e) { toastError(e); } }

  return (
    <div class="spec-root-card stack" tabIndex={0} onClick={() => openNode(spec.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(spec.id); }}>
      <div class="row between">
        <span class="title grow">{spec.title}</span>
        <Pill status={spec.status} />
      </div>
      {spec.body_md ? <div class="caption">{purposeLine(spec.body_md)}</div> : null}
      <div class="row between">
        <AgentChip agent={spec.agent} state={state} />
        <div class="actions">
          {canApproveSpec ? <Button variant="filled" onClick={(e: Event) => { e.stopPropagation(); void approveSpec(); }}>Approve spec</Button> : null}
          {canApproveTasks ? <Button variant="filled" onClick={(e: Event) => { e.stopPropagation(); void approveTasks(); }}>Approve task list</Button> : null}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npx vitest run test/SpecRoot.test.tsx test/GhostBox.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/SpecRoot.tsx src/canvas/GhostBox.tsx test/SpecRoot.test.tsx test/GhostBox.test.tsx
git commit -m "Canvas: SpecRoot card and GhostBox"
```

---

### Task 8: `Canvas` — web pan/zoom viewport

**Files:**
- Create: `dashboard/src/canvas/Canvas.tsx`, `dashboard/src/canvas/canvas.css`
- Test: `dashboard/test/Canvas.test.tsx`

**Interfaces:**
- Consumes: `CanvasData` (Task 1), `computeLayout` (Task 2), `TaskBox` (Task 6), `SpecRoot`/`GhostBox` (Task 7), `pendingCreates`/`pendingBreakdowns` (Task 3).
- Produces: `<Canvas data={CanvasData} projectId={number} projectPhase={string} needsYou={Set<number>} />` — consumed by Task 13 (`ProjectPage`, web branch).

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/test/Canvas.test.tsx
import { render, screen } from "@testing-library/preact";
import { Canvas } from "../src/canvas/Canvas";
import type { CanvasData } from "../src/canvas/canvasData";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "ready", agent: null },
  tasks: [
    { id: 2, title: "Record voice", status: "done", risk_tier: "low", owner: null, agent: "claude", body_md: "capture mic", criteria_hash: "x", block_reason: null, subtasks: [] },
    { id: 3, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude", body_md: "turn audio into notes", criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 3, carries: "audio frames" }],
};

test("renders the spec root, every task box, and an arrow label for a carried edge", () => {
  const { container } = render(<Canvas data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("Record voice")).toBeTruthy();
  expect(screen.getByText("Detect pitch")).toBeTruthy();
  expect(screen.getByText("audio frames")).toBeTruthy();
  expect(container.querySelectorAll("svg path.flow-arrow").length).toBe(1);
});

test("the canvas viewport, not the page, is what scrolls sideways", () => {
  const { container } = render(<Canvas data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(container.querySelector(".canvas-viewport")).toBeTruthy();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/Canvas.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/canvas/Canvas.tsx
import { useRef, useState } from "preact/hooks";
import type { CanvasData } from "./canvasData";
import { computeLayout } from "./flowLayout";
import { TaskBox } from "./TaskBox";
import { SpecRoot } from "./SpecRoot";
import { GhostBox } from "./GhostBox";
import { pendingCreates, pendingBreakdowns } from "./pending";
import { Icon } from "../ui/Icon";

export function Canvas({ data, projectId, projectPhase, needsYou }: { data: CanvasData; projectId: number; projectPhase: string; needsYou: Set<number> }) {
  const lay = computeLayout(data.tasks, data.edges);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number } | null>(null);

  function onPointerDown(e: PointerEvent) {
    if ((e.target as HTMLElement).closest(".task-box, .spec-root-card, button")) return;
    dragging.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging.current) return;
    setPan({ x: e.clientX - dragging.current.x, y: e.clientY - dragging.current.y });
  }
  function onPointerUp() { dragging.current = null; }
  function onWheel(e: WheelEvent) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom((z) => Math.min(2, Math.max(0.4, z - e.deltaY * 0.001)));
  }
  const fit = () => { setZoom(1); setPan({ x: 0, y: 0 }); };

  const pendingCreateGhosts = Object.entries(pendingCreates.value).filter(([, p]) => p.kind === "task");
  const breakdownGhosts = Object.entries(pendingBreakdowns.value).filter(([nodeId]) => Number(nodeId) === data.spec?.id);

  return (
    <div class="canvas-wrap">
      <div class="canvas-zoom-controls">
        <button type="button" class="icon-btn" aria-label="zoom out" onClick={() => setZoom((z) => Math.max(0.4, z - 0.1))}>−</button>
        <button type="button" class="icon-btn" aria-label="fit" onClick={fit}><Icon name="activity" /></button>
        <button type="button" class="icon-btn" aria-label="zoom in" onClick={() => setZoom((z) => Math.min(2, z + 0.1))}>+</button>
      </div>
      <div class="canvas-viewport" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onWheel={onWheel}>
        <div class="canvas-frame" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, width: lay.width + "px", height: lay.height + 96 + "px" }}>
          <div class="canvas-spec-slot"><SpecRoot spec={data.spec} projectId={projectId} taskCount={data.tasks.length} projectPhase={projectPhase} /></div>
          <svg class="canvas-arrows" width={lay.width} height={lay.height} style={{ marginTop: "96px" }}>
            {lay.arrows.map((a) => (
              <>
                <path class="flow-arrow" d={a.path} />
                {a.label ? <g transform={`translate(${a.label.x},${a.label.y})`}><rect class="arrow-label-bg" x={-40} y={-10} width={80} height={20} rx={10} /><text class="arrow-label" textAnchor="middle" dy="4">{a.label.text}</text></g> : null}
              </>
            ))}
          </svg>
          <div class="canvas-boxes" style={{ marginTop: "96px" }}>
            {data.tasks.map((t) => <TaskBox task={t} needsYou={needsYou} projectPhase={projectPhase} style={{ position: "absolute", left: lay.pos[t.id]!.x + "px", top: lay.pos[t.id]!.y + "px", width: lay.pos[t.id]!.w + "px" }} />)}
            {pendingCreateGhosts.map(([key, p]) => <GhostBox key={key} title={p.title} caption="creating…" error={p.error} />)}
            {breakdownGhosts.flatMap(([, p]) => [0, 1, 2].map((i) => <GhostBox key={p.nodeId + ":" + i} title="…" caption={`${p.agent} is breaking this down…`} logLink={p.log} />))}
          </div>
        </div>
      </div>
    </div>
  );
}
```

```css
/* dashboard/src/canvas/canvas.css */
.canvas-wrap { position: relative; overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface); min-height: 60dvh; }
.canvas-viewport { width: 100%; height: 100%; min-height: 60dvh; overflow: hidden; touch-action: none; cursor: grab; }
.canvas-viewport:active { cursor: grabbing; }
.canvas-frame { position: relative; transform-origin: 0 0; }
.canvas-spec-slot { position: absolute; top: 0; left: 0; right: 0; }
.canvas-arrows { position: absolute; pointer-events: none; }
.canvas-arrows path.flow-arrow { fill: none; stroke: var(--border); stroke-width: 1.5; }
.canvas-arrows .arrow-label-bg { fill: var(--surface-2); stroke: var(--border); }
.canvas-arrows .arrow-label { font-size: 11px; fill: var(--text-2); }
.canvas-boxes { position: absolute; top: 0; left: 0; }
.canvas-zoom-controls { position: absolute; right: 8px; bottom: 8px; z-index: 2; display: flex; gap: 4px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 4px; }
.spec-root-card, .task-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 12px 14px; cursor: pointer; box-shadow: var(--shadow); }
.spec-root-card { margin: 0 16px; }
.task-box { border-left: 4px solid var(--st-pending); }
.task-box.st-bar-ready { border-left-color: var(--st-ready); }
.task-box.st-bar-in_progress { border-left-color: var(--st-in_progress); }
.task-box.st-bar-review { border-left-color: var(--st-review); }
.task-box.st-bar-done { border-left-color: var(--st-done); }
.task-box.st-bar-blocked, .task-box.st-bar-failed { border-left-color: var(--st-blocked); }
.task-box:focus-visible, .spec-root-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.task-box.ghost { opacity: .7; border-style: dashed; cursor: default; }
.task-box.ghost-error { border-color: var(--danger); opacity: 1; }
.purpose { margin: 2px 0 6px; }
.subtask-list { display: flex; flex-direction: column; margin: 4px 0; }
.subtask-row { display: flex; align-items: center; gap: 6px; min-height: 22px; background: none; border: 0; padding: 0; width: 100%; text-align: left; cursor: pointer; font-size: 12px; }
.more-row { padding-left: 20px; }
.needs-you-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); flex: none; }
.agent-chip { display: inline-flex; align-items: center; gap: 6px; margin-top: 8px; }
.agent-chip .pulse { width: 6px; height: 6px; border-radius: 50%; background: var(--st-in_progress); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: .3; } 50% { opacity: 1; } }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/Canvas.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/Canvas.tsx src/canvas/canvas.css test/Canvas.test.tsx
git commit -m "Canvas: web pan/zoom viewport with orthogonal arrow labels"
```

---

### Task 9: `PhoneFlow` — vertical rendering

**Files:**
- Create: `dashboard/src/canvas/PhoneFlow.tsx`
- Test: `dashboard/test/PhoneFlow.test.tsx`

**Interfaces:**
- Consumes: `CanvasData` (Task 1), `stepsFromColumns` (Task 2), `TaskBox` (Task 6), `SpecRoot`/`GhostBox` (Task 7).
- Produces: `<PhoneFlow data={CanvasData} projectId={number} projectPhase={string} needsYou={Set<number>} />` — consumed by Task 13 (`ProjectPage`, phone branch).

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/test/PhoneFlow.test.tsx
import { render, screen } from "@testing-library/preact";
import { PhoneFlow } from "../src/canvas/PhoneFlow";
import type { CanvasData } from "../src/canvas/canvasData";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "x", status: "ready", agent: null },
  tasks: [
    { id: 2, title: "a", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
    { id: 3, title: "b", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
    { id: 4, title: "c", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 4, carries: null }, { from: 3, to: 4, carries: null }],
};

test("stacks steps top to bottom and labels a parallel step", () => {
  const { container } = render(<PhoneFlow data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("parallel")).toBeTruthy();
  const steps = container.querySelectorAll(".phone-step");
  expect(steps.length).toBe(2);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/PhoneFlow.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/canvas/PhoneFlow.tsx
import type { CanvasData } from "./canvasData";
import { stepsFromColumns } from "./flowLayout";
import { TaskBox } from "./TaskBox";
import { SpecRoot } from "./SpecRoot";

export function PhoneFlow({ data, projectId, projectPhase, needsYou }: { data: CanvasData; projectId: number; projectPhase: string; needsYou: Set<number> }) {
  const steps = stepsFromColumns(data.tasks, data.edges);
  return (
    <div class="stack phone-flow">
      <SpecRoot spec={data.spec} projectId={projectId} taskCount={data.tasks.length} projectPhase={projectPhase} />
      {steps.map((step) => (
        <div class="phone-step stack tight">
          {step.parallel ? <div class="caption parallel-label">parallel</div> : null}
          <div class={step.parallel ? "phone-step-row" : "stack tight"}>
            {step.tasks.map((t) => <TaskBox task={t} needsYou={needsYou} projectPhase={projectPhase} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
```

```css
/* append to dashboard/src/canvas/canvas.css */
.phone-flow .task-box { width: 100%; }
.phone-step { border-left: 2px dashed var(--border); padding-left: 10px; }
.phone-step-row { display: flex; gap: 10px; flex-wrap: wrap; }
.phone-step-row .task-box { flex: 1 1 260px; }
.parallel-label { text-transform: uppercase; letter-spacing: .04em; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/PhoneFlow.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/PhoneFlow.tsx src/canvas/canvas.css test/PhoneFlow.test.tsx
git commit -m "Canvas: PhoneFlow vertical rendering with parallel grouping"
```

---

### Task 10: `AddForm` — creation and agent breakdown

**Files:**
- Create: `dashboard/src/canvas/AddForm.tsx`
- Test: `dashboard/test/AddForm.test.tsx`

**Interfaces:**
- Consumes: `routes.nodeChildren`/`routes.nodeBreakdown` (Task 4), `pending.ts` (Task 3), `CanvasTask[]` (for the "receives from" picker).
- Produces: `<AddForm parentId={number} kind={"task"|"subtask"} candidates={CanvasTask[]} onClose={()=>void} />`, `<BreakdownButton nodeId={number} kind={"spec"|"task"} />` — consumed by Task 13 (`ProjectPage`, appended after the last column / inside each `TaskBox`'s hover menu).

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/test/AddForm.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AddForm } from "../src/canvas/AddForm";
import * as client from "../src/api/client";
import { pendingCreates } from "../src/canvas/pending";

const candidates = [{ id: 2, title: "Record voice", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] }] as const;

test("submitting posts title, body, criteria lines, and depends_on with carries", async () => {
  const spy = jest.spyOn(client, "post").mockResolvedValue({ node: { id: 9 } });
  render(<AddForm parentId={1} kind="task" candidates={candidates as any} onClose={() => {}} />);
  fireEvent.input(screen.getByPlaceholderText("title"), { target: { value: "Export MusicXML" } });
  fireEvent.input(screen.getByPlaceholderText("purpose"), { target: { value: "write the file" } });
  fireEvent.input(screen.getByPlaceholderText("one per line"), { target: { value: "writer passes\nvalid file" } });
  fireEvent.click(screen.getByLabelText("receives from Record voice"));
  fireEvent.input(screen.getByPlaceholderText("carrying ___"), { target: { value: "notes" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(spy).toHaveBeenCalled());
  expect(spy.mock.calls[0]![0]).toBe("/nodes/1/children");
  expect(spy.mock.calls[0]![1]).toEqual({
    title: "Export MusicXML", body_md: "write the file", criteria: ["writer passes", "valid file"],
    depends_on: [{ id: 2, carries: "notes" }], predicted_touches: [],
  });
});

test("a pending create shows a ghost keyed to the parent while the request is in flight", () => {
  render(<AddForm parentId={1} kind="task" candidates={[]} onClose={() => {}} />);
  fireEvent.input(screen.getByPlaceholderText("title"), { target: { value: "x" } });
  jest.spyOn(client, "post").mockReturnValue(new Promise(() => {})); // never resolves
  fireEvent.click(screen.getByText("Save"));
  expect(pendingCreates.value["task:1:new"]).toBeDefined();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/AddForm.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/canvas/AddForm.tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toastError } from "../state";
import type { CanvasTask } from "./canvasData";
import { startCreate, resolveCreate, failCreate } from "./pending";
import { Button } from "../ui/Button";

type Receives = { id: number; carries: string };

export function AddForm({ parentId, kind, candidates, onClose }: { parentId: number; kind: "task" | "subtask"; candidates: CanvasTask[]; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [criteria, setCriteria] = useState("");
  const [receives, setReceives] = useState<Receives[]>([]);

  function toggle(id: number) {
    setReceives((r) => (r.some((x) => x.id === id) ? r.filter((x) => x.id !== id) : [...r, { id, carries: "" }]));
  }
  function setCarries(id: number, carries: string) {
    setReceives((r) => r.map((x) => (x.id === id ? { ...x, carries } : x)));
  }

  async function save() {
    if (!title.trim()) return;
    const key = `${kind}:${parentId}:new`;
    startCreate(key, { kind, parentId, title });
    try {
      await post(routes.nodeChildren(parentId), {
        title, body_md: purpose, criteria: criteria.split("\n").map((l) => l.trim()).filter(Boolean),
        depends_on: receives.map((r) => ({ id: r.id, carries: r.carries || null })), predicted_touches: [],
      });
      resolveCreate(key);
      refresh();
      onClose();
    } catch (e) { failCreate(key, e instanceof Error ? e.message : String(e)); toastError(e); }
  }

  return (
    <form class="card stack tight add-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <input placeholder="purpose" value={purpose} onInput={(e) => setPurpose((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one per line" value={criteria} onInput={(e) => setCriteria((e.target as HTMLTextAreaElement).value)} />
      {candidates.length ? (
        <div class="stack tight">
          <div class="caption">Receives from</div>
          {candidates.map((c) => {
            const picked = receives.find((r) => r.id === c.id);
            return (
              <div class="row">
                <label class="row" style={{ flex: 1 }}>
                  <input type="checkbox" aria-label={`receives from ${c.title}`} style={{ width: "auto", minHeight: 0 }} checked={!!picked} onChange={() => toggle(c.id)} />
                  {c.title}
                </label>
                {picked ? <input placeholder="carrying ___" value={picked.carries} onInput={(e) => setCarries(c.id, (e.target as HTMLInputElement).value)} /> : null}
              </div>
            );
          })}
        </div>
      ) : null}
      <div class="actions"><Button type="submit" variant="filled">Save</Button><Button variant="plain" onClick={onClose}>Cancel</Button></div>
    </form>
  );
}

export function BreakdownButton({ nodeId, disabled, caption }: { nodeId: number; disabled?: boolean; caption?: string }) {
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setError(null);
    try {
      const r = await post<{ spawned: { agent: string; log: string } }>(routes.nodeBreakdown(nodeId), {});
      startBreakdownGhosts(nodeId, r.spawned.agent, r.spawned.log);
      refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <div class="stack tight">
      <Button variant="outline" disabled={disabled} onClick={run}>✨ Break down with agent</Button>
      {disabled && caption ? <div class="caption">{caption}</div> : null}
      {error ? <div class="caption danger">{error}</div> : null}
    </div>
  );
}

// ProjectPage supplies the current children of `nodeId` when it calls this,
// so the ghost-clearing check in pending.ts has a starting point to diff
// against on the next refresh.
import { startBreakdown } from "./pending";
function startBreakdownGhosts(nodeId: number, agent: string, log: string): void {
  startBreakdown(nodeId, { nodeId, agent, log, startChildIds: new Set() });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/AddForm.test.tsx`
Expected: PASS. (`vitest` supports `jest.spyOn`/`jest` globals via its Jest-compat layer once `globals: true` is set in `dashboard/vite.config.ts` — confirm this is already the case by checking existing tests' use of `jest.spyOn`/`vi.spyOn`; if the codebase's convention is `vi` instead of `jest`, use `vi.spyOn` throughout this task's test to match.)

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/canvas/AddForm.tsx test/AddForm.test.tsx
git commit -m "Canvas: AddForm (task/subtask creation) and agent breakdown"
```

---

### Task 11: `cardsFromInbox` — pure mapping

**Files:**
- Create: `dashboard/src/cards/cardsFromInbox.ts`
- Test: `dashboard/test/cardsFromInbox.test.ts`

**Note on the spec vs. the actual API:** the design spec's Notification cards table lists "Spec awaiting approval" and "Task list awaiting Gate 2" and "Plan revision proposed" as inbox-sourced cards, and says cards "come from the existing `/inbox` data, with no new data source." Reading `src/muvue/api/app.py`'s `inbox()` handler shows `/inbox`'s actual response has no such categories — `questions`, `review`, `unverified_external`, `structure_updates`, `blocked`, `awaiting_approval`, `signals`, `audit_items`, `unattributed_commits` are all it returns. The dashboard-redesign plan's own `SpecCard.tsx` already worked around this the same way: deriving "spec pending" and "gate2 pending" client-side from the spec node's own `status` and the project's `phase`, not from `/inbox`. This task follows that same precedent — `cardsFromInbox` takes a small `ctx` object (spec, task count, phase, and the project's revision list) alongside `Inbox`, and synthesizes those two card kinds plus "revision" the same way. This is the smallest correction that keeps the design's *behavior* (those cards appear and act as described) without inventing a backend change the spec didn't actually ask for.

**Files (continued):**
- Test: `dashboard/test/cardsFromInbox.test.ts`

**Interfaces:**
- Consumes: `Inbox` type (moved here from the deleted `inbox/InboxPage.tsx`), `CanvasSpec` (Task 1), a `Revision` type `{ n: number; approved_at: string | null }` (matches `plan_revisions` rows returned by `GET /projects/{id}/revisions`).
- Produces: `Card`, `CardKind`, `cardsFromInbox(inbox, ctx) -> Card[]` — consumed by Task 12 (`Card`/`CardRail`), Task 13 (`ProjectPage`).

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/test/cardsFromInbox.test.ts
import { cardsFromInbox, type Inbox } from "../src/cards/cardsFromInbox";

const emptyInbox: Inbox = { questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] };
const nodesById = { 5: { title: "Detect pitch", project_id: 1 } };

test("a pending spec synthesizes a spec_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: { id: 1, title: "Voxscore", body_md: null, status: "pending", agent: null }, taskCount: 0, projectPhase: "planning", revisions: [], nodesById });
  expect(cards.map((c) => c.kind)).toEqual(["spec_review"]);
  expect(cards[0]!.nodeId).toBe(1);
});

test("an approved spec with tasks in planning phase synthesizes a gate2_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: { id: 1, title: "Voxscore", body_md: null, status: "ready", agent: null }, taskCount: 2, projectPhase: "planning", revisions: [], nodesById });
  expect(cards.map((c) => c.kind)).toEqual(["gate2_review"]);
});

test("a question maps to a question card with the node's title resolved from nodesById", () => {
  const inbox: Inbox = { ...emptyInbox, questions: [{ id: 9, node_id: 5, text: "which encoder?", default_answer: "opus", default_ok: true }] };
  const cards = cardsFromInbox(inbox, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById });
  expect(cards[0]).toMatchObject({ kind: "question", nodeId: 5, title: "Detect pitch", context: "which encoder?" });
});

test("review/blocked nodes map to task_review/blocked cards", () => {
  const node = { id: 5, project_id: 1, parent_id: null, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "claude" };
  const cards = cardsFromInbox({ ...emptyInbox, review: [node] }, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById });
  expect(cards[0]).toMatchObject({ kind: "task_review", nodeId: 5, agent: "claude" });
});

test("an unapproved revision synthesizes a revision card; an approved one does not", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [{ n: 1, approved_at: null }, { n: 2, approved_at: "2026-01-01" }], nodesById });
  expect(cards.map((c) => c.kind)).toEqual(["revision"]);
});

test("structure/audit/signal/unattributed events all collapse into ack cards", () => {
  const ev = { id: 1, ts: "t", node_id: null, project_id: 1, type: "inbox.structure_update_ready", payload: "{}", acked_at: null };
  const cards = cardsFromInbox({ ...emptyInbox, structure_updates: [ev] }, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById });
  expect(cards[0]!.kind).toBe("ack");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/cardsFromInbox.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```typescript
// dashboard/src/cards/cardsFromInbox.ts
import type { NodeRow } from "../state";
import type { CanvasSpec } from "../canvas/canvasData";

type Ev = { id: number; ts: string; node_id: number | null; project_id: number | null; type: string; payload: string };
type Question = { id: number; node_id: number; text: string; default_answer: string | null; default_ok: boolean };
export type Inbox = {
  questions: Question[]; review: NodeRow[]; unverified_external: { node_id: number; title: string }[];
  structure_updates: Ev[]; blocked: NodeRow[]; awaiting_approval: NodeRow[]; signals: Ev[]; audit_items: Ev[]; unattributed_commits: Ev[];
};
export type Revision = { n: number; approved_at: string | null };
export type NodesById = Record<number, { title: string; project_id: number }>;

export type CardKind = "spec_review" | "gate2_review" | "task_review" | "question" | "blocked" | "revision" | "ack";
export type Card = { id: string; kind: CardKind; nodeId: number | null; projectId: number | null; title: string; context: string; agent: string; ts: string; raw: unknown };

export type Ctx = { spec: CanvasSpec | null; taskCount: number; projectPhase: string; revisions: Revision[]; nodesById: NodesById };

function payloadOf(ev: Ev): Record<string, unknown> { try { return JSON.parse(ev.payload || "{}"); } catch { return {}; } }

export function cardsFromInbox(inbox: Inbox, ctx: Ctx): Card[] {
  const cards: Card[] = [];
  if (ctx.spec && ctx.spec.status === "pending") {
    cards.push({ id: "spec_review:" + ctx.spec.id, kind: "spec_review", nodeId: ctx.spec.id, projectId: ctx.nodesById[ctx.spec.id]?.project_id ?? null, title: ctx.spec.title, context: "spec awaiting approval", agent: "", ts: "" });
  }
  if (ctx.spec && ctx.spec.status !== "pending" && ctx.taskCount > 0 && ctx.projectPhase === "planning") {
    cards.push({ id: "gate2_review:" + ctx.spec.id, kind: "gate2_review", nodeId: ctx.spec.id, projectId: ctx.nodesById[ctx.spec.id]?.project_id ?? null, title: ctx.spec.title, context: `${ctx.taskCount} tasks awaiting Gate 2`, agent: "", ts: "" });
  }
  for (const q of inbox.questions) {
    const n = ctx.nodesById[q.node_id];
    cards.push({ id: "question:" + q.id, kind: "question", nodeId: q.node_id, projectId: n?.project_id ?? null, title: n?.title ?? `#${q.node_id}`, context: q.text, agent: "", ts: "" });
  }
  for (const n of inbox.review) cards.push({ id: "task_review:" + n.id, kind: "task_review", nodeId: n.id, projectId: n.project_id, title: n.title, context: "in review", agent: n.owner ?? "", ts: "" });
  for (const n of inbox.blocked) cards.push({ id: "blocked:" + n.id, kind: "blocked", nodeId: n.id, projectId: n.project_id, title: n.title, context: n.block_reason ?? "blocked", agent: n.owner ?? "", ts: "" });
  for (const r of ctx.revisions.filter((r) => r.approved_at === null)) {
    cards.push({ id: "revision:" + r.n, kind: "revision", nodeId: null, projectId: null, title: `Revision ${r.n}`, context: "plan revision proposed", agent: "", ts: "" });
  }
  const ackSources: Ev[] = [...inbox.structure_updates, ...inbox.audit_items, ...inbox.signals, ...inbox.unattributed_commits];
  for (const ev of ackSources) {
    const p = payloadOf(ev);
    cards.push({ id: "ack:" + ev.id, kind: "ack", nodeId: ev.node_id, projectId: ev.project_id, title: ev.type, context: String(p.message ?? ev.type), agent: "", ts: ev.ts, raw: ev });
  }
  for (const u of inbox.unverified_external) cards.push({ id: "ack:unverified:" + u.node_id, kind: "ack", nodeId: u.node_id, projectId: ctx.nodesById[u.node_id]?.project_id ?? null, title: u.title, context: "unverified external criteria", agent: "", ts: "" });
  return cards;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run test/cardsFromInbox.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/cards/cardsFromInbox.ts test/cardsFromInbox.test.ts
git commit -m "Cards: pure mapping from /inbox (plus spec/gate2/revision context) to cards"
```

---

### Task 12: `Card`, `Discuss`, `CardRail`

**Files:**
- Create: `dashboard/src/cards/Card.tsx`, `dashboard/src/cards/Discuss.tsx`, `dashboard/src/cards/CardRail.tsx`, `dashboard/src/cards/cards.css`
- Test: `dashboard/test/Card.test.tsx`

**Interfaces:**
- Consumes: `Card` (Task 11), `routes.nodeApprove`/`nodeReject`/`questionAnswer`/`eventAck`/`nodeComment` (existing), `openNode` (Task 4).
- Produces: `<Card card={Card} onActed={()=>void} />`, `<CardRail cards={Card[]} />` — consumed by Task 13 (`ProjectPage`).

- [ ] **Step 1: Write the failing test**

```tsx
// dashboard/test/Card.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { Card } from "../src/cards/Card";
import * as client from "../src/api/client";

const base = { projectId: 1, title: "Detect pitch", context: "in review", agent: "claude", ts: "", raw: {} } as const;

test("approve calls the approve route for the card's kind and shows a spinner while pending", async () => {
  const spy = jest.spyOn(client, "post").mockResolvedValue({});
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Approve"));
  await waitFor(() => expect(spy).toHaveBeenCalledWith("/nodes/5/approve", { target: "review" }));
});

test("reject disables Send until a reason is typed", () => {
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Reject"));
  expect(screen.getByText("Send")).toBeDisabled();
  fireEvent.input(screen.getByPlaceholderText("why?"), { target: { value: "needs more tests" } });
  expect(screen.getByText("Send")).not.toBeDisabled();
});

test("a question card's Discuss field doubles as the answer box", async () => {
  const spy = jest.spyOn(client, "post").mockResolvedValue({});
  render(<Card card={{ id: "question:9", kind: "question", nodeId: 5, ...base, context: "which encoder?" }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Discuss"));
  fireEvent.input(screen.getByPlaceholderText("reply"), { target: { value: "opus" } });
  fireEvent.click(screen.getByText("Send"));
  await waitFor(() => expect(spy).toHaveBeenCalled());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run test/Card.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/cards/Discuss.tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { toastError } from "../state";
import { Button } from "../ui/Button";
import type { Card } from "./cardsFromInbox";
import type { NodeDetail } from "../node/NodeSheet";

export function Discuss({ card, onSent }: { card: Card; onSent: () => void }) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [reply, setReply] = useState("");
  useEffect(() => { if (card.nodeId) api<NodeDetail>(routes.node(card.nodeId)).then(setDetail, toastError); }, [card.nodeId]);
  async function send() {
    if (!reply.trim()) return;
    try {
      if (card.kind === "question") await post(routes.questionAnswer(Number(card.id.split(":")[1])), { text: reply });
      else if (card.nodeId) await post(routes.nodeComment(card.nodeId), { text: reply });
      setReply(""); onSent();
    } catch (e) { toastError(e); }
  }
  return (
    <div class="stack tight discuss">
      {detail ? detail.notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>) : null}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" onClick={send}>Send</Button>
      </div>
    </div>
  );
}
```

```tsx
// dashboard/src/cards/Card.tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { toast, toastError } from "../state";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Discuss } from "./Discuss";
import type { Card as CardT } from "./cardsFromInbox";

const APPROVE_TARGET: Record<string, string> = { spec_review: "spec", gate2_review: "gate2", task_review: "review" };
const CAPTION: Record<CardT["kind"], string> = { spec_review: "Spec awaiting approval", gate2_review: "Task list awaiting Gate 2", task_review: "Task in review", question: "Agent question", blocked: "Blocked", revision: "Plan revision proposed", ack: "Update" };

export function Card({ card, onActed }: { card: CardT; onActed: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [discussing, setDiscussing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setError(null);
    try { await fn(); toast(label.toLowerCase()); onActed(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  }
  async function approve() {
    if (card.kind === "revision") return run("Approve", () => post(routes.approveRevision(Number(card.id.split(":")[1])), {}));
    if (card.kind === "ack") return run("Acknowledge", () => post(routes.eventAck(Number(card.id.split(":").pop()))));
    const target = APPROVE_TARGET[card.kind];
    if (!target || !card.nodeId) return;
    return run("Approve", () => post(routes.nodeApprove(card.nodeId!), { target }));
  }
  async function reject() {
    if (!reason.trim() || !card.nodeId) return;
    return run("Send back", () => post(routes.nodeReject(card.nodeId!), { feedback: reason }));
  }

  return (
    <div class="card stack tight notif-card">
      <div class="row between">
        <span class="caption">{CAPTION[card.kind]}</span>
        <span class="caption">{card.ts}</span>
      </div>
      <button type="button" class="notif-title" disabled={!card.nodeId} onClick={() => card.nodeId && openNode(card.nodeId)}>{card.title}</button>
      <div>{card.context}</div>
      {card.agent ? <div class="caption">{card.agent}</div> : null}
      {error ? <div class="callout danger">{error}</div> : null}
      {card.kind !== "ack" ? (
        <div class="actions">
          <Button variant="filled" disabled={!!busy} onClick={approve}>{busy === "Approve" ? "…" : "Approve"}</Button>
          {card.kind !== "revision" ? <Button variant="danger" disabled={!!busy} onClick={() => setRejecting((r) => !r)}>Reject</Button> : null}
          {card.kind !== "revision" ? <Button variant="plain" onClick={() => setDiscussing((d) => !d)}>{card.kind === "question" ? "Answer" : "Discuss"}</Button> : null}
        </div>
      ) : (
        <div class="actions"><Button variant="filled" disabled={!!busy} onClick={approve}>{busy ? "…" : "Acknowledge"}</Button></div>
      )}
      {rejecting ? (
        <div class="stack tight">
          <input placeholder="why?" value={reason} onInput={(e) => setReason((e.target as HTMLInputElement).value)} />
          <Button variant="danger" disabled={!reason.trim()} onClick={reject}>Send</Button>
        </div>
      ) : null}
      {discussing ? <Discuss card={card} onSent={onActed} /> : null}
    </div>
  );
}
```

```tsx
// dashboard/src/cards/CardRail.tsx
import { useState } from "preact/hooks";
import type { Card as CardT } from "./cardsFromInbox";
import { Card } from "./Card";
import { refresh } from "../state";

export function CardRail({ cards }: { cards: CardT[] }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!cards.length) return <div class="card-rail-empty caption">Nothing waiting on you</div>;
  if (collapsed) return <button type="button" class="card-rail-pill" onClick={() => setCollapsed(false)}>{cards.length} need you</button>;
  return (
    <div class="card-rail stack tight">
      <div class="row between"><span class="caption">Needs you · {cards.length}</span><button type="button" class="icon-btn" aria-label="collapse" onClick={() => setCollapsed(true)}>—</button></div>
      {cards.map((c) => <Card key={c.id} card={c} onActed={refresh} />)}
    </div>
  );
}
```

```css
/* dashboard/src/cards/cards.css */
.card-rail { width: 320px; flex: none; padding: 12px; border-left: 1px solid var(--border); overflow-y: auto; }
.card-rail-empty { padding: 12px; }
.card-rail-pill { position: sticky; top: 8px; align-self: flex-end; background: var(--accent); color: var(--on-accent); border: 0; border-radius: 999px; padding: 8px 16px; min-height: 44px; cursor: pointer; }
.notif-card { border-left: 3px solid var(--accent); }
.notif-title { background: none; border: 0; padding: 0; text-align: left; font-weight: 600; cursor: pointer; color: var(--text); min-height: 44px; }
.notif-title:disabled { cursor: default; }
@media (max-width: 899px) { .card-rail { width: 100%; border-left: 0; border-bottom: 1px solid var(--border); } }
```

Add to `routes.ts` (used by `Card.tsx`'s `approve()` for the `revision` kind, which needs `n` and `project_id` — since `Card` only carries the revision number in its id, and `POST /nodes/{node_id}/approve {target:"revision", n}` is keyed by the **project's node id**, not a project id directly, extend `cardsFromInbox`'s revision card to carry `nodeId` as the project id and add this route):

```typescript
// dashboard/src/api/routes.ts — add
  approveRevision: (projectId: number) => `/nodes/${projectId}/approve`,
```

- [ ] **Step 4: Amend Task 11's revision card to carry the project id as `nodeId`**

```typescript
// dashboard/src/cards/cardsFromInbox.ts — change the revision loop to (ctx needs a projectId field)
  for (const r of ctx.revisions.filter((r) => r.approved_at === null)) {
    cards.push({ id: "revision:" + r.n, kind: "revision", nodeId: ctx.projectId, projectId: ctx.projectId, title: `Revision ${r.n}`, context: "plan revision proposed", agent: "", ts: "" });
  }
```

Add `projectId: number` to the `Ctx` type in the same file, and update `dashboard/test/cardsFromInbox.test.ts`'s revision test to pass `projectId: 1` in `ctx` and assert `nodeId: 1` on the resulting card.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard && npx vitest run test/cardsFromInbox.test.ts test/Card.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd dashboard && git add src/cards/ dashboard/src/api/routes.ts test/Card.test.tsx test/cardsFromInbox.test.ts
git commit -m "Cards: Card, Discuss and CardRail"
```

---

### Task 13: Node sheet extensions — `Runs`, `Flow`, `Discussion`, `Details`, `SpecBody`

**Files:**
- Create: `dashboard/src/node/Runs.tsx`, `dashboard/src/node/Flow.tsx`, `dashboard/src/node/Discussion.tsx`, `dashboard/src/node/Details.tsx`, `dashboard/src/node/SpecBody.tsx`
- Modify: `dashboard/src/node/NodeSheet.tsx`, `dashboard/src/node/Actions.tsx`
- Test: `dashboard/test/Runs.test.tsx`, `dashboard/test/NodeSheet.test.tsx` (extend existing)

**Interfaces:**
- Consumes: `routes.nodeRuns`, `routes.nodeRemove`, `routes.nodeEdit`, `routes.nodeBreakdown` (Task 4), `NodeDetail` (existing, extended).
- Produces: node sheet order per spec: title/pill/chip → Actions (approve/reject/start/retry/handoff/merge/**break down**/**remove**) → purpose+criteria (or `SpecBody` for a spec) → `Flow` → subtasks (already shown via `Overview`'s criteria block, extended) → `Runs` → `Discussion` → `Details` disclosure.

- [ ] **Step 1: Write the failing tests**

```tsx
// dashboard/test/Runs.test.tsx
import { render, screen, waitFor } from "@testing-library/preact";
import { Runs } from "../src/node/Runs";
import * as client from "../src/api/client";

test("shows each run/breakdown event with agent, outcome and a log tail toggle", async () => {
  jest.spyOn(client, "api").mockResolvedValue({ runs: [
    { id: 1, ts: "2026-09-28T00:00:00Z", type: "node.start", node_id: 5, actor: "runner:claude", payload: "{}" },
    { id: 2, ts: "2026-09-28T00:01:00Z", type: "node.done", node_id: 5, actor: "runner:claude", payload: "{}" },
    { id: 3, ts: "2026-09-28T00:02:00Z", type: "breakdown.finished", node_id: 5, actor: "agent", payload: "{\"created\":[6,7]}" },
  ] });
  render(<Runs nodeId={5} />);
  await waitFor(() => screen.getByText("node.done"));
  expect(screen.getByText("breakdown.finished")).toBeTruthy();
});
```

```tsx
// dashboard/test/NodeSheet.test.tsx — append these to the existing file
test("a task before Gate 2 shows a Remove action", async () => {
  // ... mirror the existing NodeSheet.test.tsx's mocking pattern for `api`, returning
  // a NodeDetail whose node has criteria_hash: null and status: "ready", then assert
  // screen.getByText("Remove") is present after Actions renders.
});
test("a spec node's sheet shows SpecBody instead of the purpose/criteria section", async () => {
  // ... mock a NodeDetail with node.kind === "spec", assert the line-numbered
  // spec text (SpecBody's ".spec-line" rows) renders in place of Overview's criteria block.
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npx vitest run test/Runs.test.tsx test/NodeSheet.test.tsx`
Expected: FAIL — `Runs` module not found; the two new `NodeSheet` assertions fail against the current sheet

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/node/Runs.tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { Logs } from "./Logs";

type Run = { id: number; ts: string; type: string; node_id: number; actor: string; payload: string };

export function Runs({ nodeId }: { nodeId: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { api<{ runs: Run[] }>(routes.nodeRuns(nodeId)).then((r) => setRuns(r.runs)); }, [nodeId]);
  if (!runs) return <p class="muted">loading…</p>;
  if (!runs.length) return <p class="muted">no runs yet</p>;
  return (
    <div class="list">
      {runs.map((r) => (
        <div class="list-row" style={{ cursor: "default" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
          <span class="grow">
            <span class="title">{r.type}</span>
            <span class="caption" style={{ display: "block" }}>{r.actor.replace(/^runner:/, "")} · {r.ts}</span>
          </span>
        </div>
      ))}
      {open !== null ? <Logs nodeId={nodeId} /> : null}
    </div>
  );
}
```

```tsx
// dashboard/src/node/Flow.tsx
import { openNode } from "../router";

type Link = { id: number; title: string; carries: string | null };

export function Flow({ receivesFrom, sendsTo }: { receivesFrom: Link[]; sendsTo: Link[] }) {
  if (!receivesFrom.length && !sendsTo.length) return null;
  return (
    <div class="stack tight">
      {receivesFrom.map((l) => <button type="button" class="list-row" onClick={() => openNode(l.id)}>receives {l.carries ? l.carries + " " : ""}from #{l.id} {l.title}</button>)}
      {sendsTo.map((l) => <button type="button" class="list-row" onClick={() => openNode(l.id)}>sends {l.carries ? l.carries + " " : ""}to #{l.id} {l.title}</button>)}
    </div>
  );
}
```

```tsx
// dashboard/src/node/Discussion.tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toastError } from "../state";
import { Button } from "../ui/Button";

type Note = { kind: string; text: string; created_at: string };

export function Discussion({ nodeId, notes }: { nodeId: number; notes: Note[] }) {
  const [reply, setReply] = useState("");
  async function send() {
    if (!reply.trim()) return;
    try { await post(routes.nodeComment(nodeId), { text: reply }); setReply(""); refresh(); } catch (e) { toastError(e); }
  }
  return (
    <div class="stack tight">
      {notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>)}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" onClick={send}>Send</Button>
      </div>
    </div>
  );
}
```

```tsx
// dashboard/src/node/Details.tsx
import { useState } from "preact/hooks";
import { DiffView } from "./DiffView";
import { Logs } from "./Logs";

export function Details({ nodeId, predictedTouches }: { nodeId: number; predictedTouches: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Details</summary>
      {open ? (
        <div class="stack">
          <section><h3>Predicted touches</h3><pre>{predictedTouches.join("\n") || "none"}</pre></section>
          <section><h3>Diff</h3><DiffView nodeId={nodeId} /></section>
          <section><h3>Logs</h3><Logs nodeId={nodeId} /></section>
        </div>
      ) : null}
    </details>
  );
}
```

```tsx
// dashboard/src/node/SpecBody.tsx — moved from the deleted spec/SpecPage.tsx, unwrapped from the page shell
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";

export function SpecBody({ nodeId, bodyMd, notes }: { nodeId: number; bodyMd: string | null; notes: { kind: string; text: string }[] }) {
  const [openLine, setOpenLine] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [general, setGeneral] = useState("");
  async function send(text: string, line?: number) {
    if (!text.trim()) return;
    try {
      await post(routes.nodeComment(nodeId), line === undefined ? { text } : { text, line });
      toast("comment saved"); setDraft(""); setGeneral(""); setOpenLine(null); refresh();
    } catch (e) { toastError(e); }
  }
  const byLine: Record<number, string[]> = {};
  const generalNotes: string[] = [];
  for (const note of notes.filter((n) => n.kind === "feedback")) {
    const m = note.text.match(/^\[L(\d+)\] ([\s\S]*)$/);
    if (m) (byLine[Number(m[1])] ??= []).push(m[2]!); else generalNotes.push(note.text);
  }
  const lines = (bodyMd || "").split("\n");
  return (
    <div class="stack">
      <div class="caption">Tap a line to comment on it. The agent sees comments in its brief.</div>
      <div class="card spec-body">
        {lines.map((text, i) => {
          const n = i + 1;
          return (
            <>
              <button type="button" class="spec-line" onClick={() => setOpenLine(openLine === n ? null : n)}>
                <span class="ln">{n}</span><span class="tx">{text || " "}</span>
              </button>
              {(byLine[n] ?? []).map((c) => <div class="callout spec-comment">{c}</div>)}
              {openLine === n && authed.value ? (
                <form class="spec-form row" onSubmit={(e) => { e.preventDefault(); void send(draft, n); }}>
                  <input placeholder={`comment on line ${n}`} value={draft} onInput={(e) => setDraft((e.target as HTMLInputElement).value)} />
                  <Button type="submit" variant="filled">Comment</Button>
                </form>
              ) : null}
            </>
          );
        })}
      </div>
      {generalNotes.length ? <section class="stack tight"><h3>General comments</h3>{generalNotes.map((c) => <div class="callout">{c}</div>)}</section> : null}
      {authed.value ? (
        <form class="stack tight" onSubmit={(e) => { e.preventDefault(); void send(general); }}>
          <textarea placeholder="general comment on this spec" value={general} onInput={(e) => setGeneral((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button type="submit">Comment</Button></div>
        </form>
      ) : null}
    </div>
  );
}
```

```tsx
// dashboard/src/node/NodeSheet.tsx — replace the whole file
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick, type NodeRow } from "../state";
import { route, closeNode } from "../router";
import { Sheet } from "../ui/Sheet";
import { Pill, Tier } from "../ui/Pill";
import { Actions } from "./Actions";
import { Overview } from "./Overview";
import { SpecBody } from "./SpecBody";
import { Flow } from "./Flow";
import { Runs } from "./Runs";
import { Discussion } from "./Discussion";
import { Details } from "./Details";

export type NodeDetail = {
  node: NodeRow & { body_md: string | null; criteria_json: string | null; criteria_mode: string; summary: string | null; block_reason: string | null; criteria_hash: string | null; deleted_at: string | null };
  notes: { kind: string; pinned: number | boolean; created_at: string; text: string }[];
  commits: { sha: string }[];
  predicted_touches: string[];
  verification: string;
};

export function NodeSheet() {
  const id = Number(route.value.query.get("node"));
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<NodeDetail>(routes.node(id)).then(
      (d) => { if (!alive) return; setDetail(d); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [id, refreshTick.value]);
  const title = detail ? `#${detail.node.id} ${detail.node.title}` : `#${id}`;
  return (
    <Sheet title={title} onClose={closeNode}>
      {error ? <div class="callout danger">{error}</div> : !detail ? <p class="muted">loading…</p> : (
        <div class="stack">
          <div class="row">
            <Pill status={detail.node.status} />
            <Tier tier={detail.node.risk_tier} />
            <span class="caption">{detail.node.kind}{detail.node.owner ? ` · owner ${detail.node.owner}` : ""}{detail.node.parent_id ? ` · parent #${detail.node.parent_id}` : ""}</span>
          </div>
          {detail.node.block_reason ? <div class="callout danger">blocked: {detail.node.block_reason}</div> : null}
          <Actions detail={detail} onDone={closeNode} />
          {detail.node.kind === "spec" ? <SpecBody nodeId={id} bodyMd={detail.node.body_md} notes={detail.notes} /> : <Overview detail={detail} />}
          <Flow receivesFrom={[]} sendsTo={[]} />
          <section><h3>Runs</h3><Runs nodeId={id} /></section>
          <section><h3>Discussion</h3><Discussion nodeId={id} notes={detail.notes} /></section>
          <Details nodeId={id} predictedTouches={detail.predicted_touches} />
        </div>
      )}
    </Sheet>
  );
}
```

Note: `Flow`'s `receivesFrom`/`sendsTo` are left empty here deliberately — wiring them to the project's dep edges needs the canvas's already-fetched `CanvasData`, which `NodeSheet` (opened from anywhere, including a URL with no canvas loaded yet) does not have. Task 15 passes them down from `ProjectPage`, which does have the edges, as a prop threaded through `NodeSheet`; this task's version compiles and renders correctly with empty arrays (Flow returns `null` when both are empty, matching a node with no deps).

```tsx
// dashboard/src/node/Actions.tsx — add remove/edit/breakdown actions to the existing file's `buttons` array and handlers
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { StartPicker } from "./StartPicker";
import type { NodeDetail } from "./NodeSheet";

export function Actions({ detail, onDone }: { detail: NodeDetail; onDone: () => void }) {
  const n = detail.node;
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [starting, setStarting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [breakingDown, setBreakingDown] = useState(false);
  if (!authed.value) return null;

  async function approve(target: string, label: string) {
    try { await post(routes.nodeApprove(n.id), { target }); toast(label); refresh(); onDone(); } catch (e) { toastError(e); }
  }
  async function reject() {
    if (!feedback.trim()) return;
    try { await post(routes.nodeReject(n.id), { feedback }); toast("sent back with feedback"); refresh(); onDone(); } catch (e) { toastError(e); }
  }
  async function remove() {
    try { await post(routes.nodeRemove(n.id)); toast("removed"); refresh(); onDone(); } catch (e) { toastError(e); }
  }
  async function breakDown() {
    setBreakingDown(true);
    try { await post(routes.nodeBreakdown(n.id)); toast("breakdown started"); refresh(); } catch (e) { toastError(e); } finally { setBreakingDown(false); }
  }

  const buttons = [];
  if (n.status === "review") {
    buttons.push(<Button variant="filled" onClick={() => approve("review", "approved")}>Approve</Button>);
    buttons.push(<Button variant="danger" onClick={() => setRejecting(true)}>Reject</Button>);
  }
  if (n.status === "awaiting_approval") buttons.push(<Button variant="filled" onClick={() => approve("node", "criteria approved")}>Approve changed criteria</Button>);
  if (n.kind === "spec" && n.status === "pending") buttons.push(<Button variant="filled" onClick={() => approve("spec", "spec approved")}>Approve spec</Button>);
  if (n.status === "ready") buttons.push(<Button variant="filled" onClick={() => setStarting(true)}>Start with agent</Button>);
  if ((n.kind === "spec" || n.kind === "task") && n.deleted_at === null) buttons.push(<Button variant="outline" disabled={breakingDown} onClick={breakDown}>✨ Break down with agent</Button>);
  if (n.criteria_hash === null && n.deleted_at === null && n.kind !== "spec") buttons.push(<Button variant="danger" onClick={() => setRemoving(true)}>Remove</Button>);
  if (!buttons.length) return null;

  return (
    <div class="stack tight">
      <div class="actions">{buttons}</div>
      {rejecting ? (
        <div class="stack tight">
          <textarea placeholder="what should change?" value={feedback} onInput={(e) => setFeedback((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button variant="danger" onClick={reject}>Send</Button><Button variant="plain" onClick={() => setRejecting(false)}>Cancel</Button></div>
        </div>
      ) : null}
      {starting ? <StartPicker nodeId={n.id} onClose={() => setStarting(false)} onStarted={onDone} /> : null}
      {removing ? <Confirm title="Remove this task?" body="It and its subtasks are soft-deleted; this cannot be undone from the page." confirmLabel="Remove" danger onConfirm={() => { setRemoving(false); void remove(); }} onCancel={() => setRemoving(false)} /> : null}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npx vitest run test/Runs.test.tsx test/NodeSheet.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd dashboard && git add src/node/ test/Runs.test.tsx test/NodeSheet.test.tsx
git commit -m "Node sheet: Runs, Flow, Discussion, Details, SpecBody, remove/breakdown actions"
```

---

### Task 14: `ProjectPage`, `StatusLine`, `NewProject`, `AgentsSheet`, `HistorySheet`

**Files:**
- Create: `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/project/StatusLine.tsx`, `dashboard/src/project/NewProject.tsx`, `dashboard/src/project/AgentsSheet.tsx`, `dashboard/src/project/HistorySheet.tsx`
- Modify: `dashboard/src/shell/ProjectMenu.tsx` (add Agents/History entries)
- Test: `dashboard/test/ProjectPage.test.tsx`, `dashboard/test/StatusLine.test.tsx`, `dashboard/test/AgentsSheet.test.tsx`

**Interfaces:**
- Consumes: `buildCanvasData` (Task 1), `Canvas`/`PhoneFlow` (Task 8/9), `CardRail`/`cardsFromInbox` (Task 11/12), `useApi` (Task 4), `routes.agentsStatus`/`projectRun` (Task 4).
- Produces: `<ProjectPage />` — the app's only page, registered in `app.tsx` (Task 15).

- [ ] **Step 1: Write the failing tests**

```tsx
// dashboard/test/StatusLine.test.tsx
import { render, screen } from "@testing-library/preact";
import { StatusLine } from "../src/project/StatusLine";

test("shows phase, done/total, busy agent count, and turns amber past 80% of budget", () => {
  render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={8.5} budget={10} />);
  expect(screen.getByText(/executing/)).toBeTruthy();
  expect(screen.getByText(/3\/9 done/)).toBeTruthy();
  expect(screen.getByText(/2 agents busy/)).toBeTruthy();
  const { container } = render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={8.5} budget={10} />);
  expect(container.querySelector(".spend-amber")).toBeTruthy();
});

test("spend turns red at the budget cap", () => {
  const { container } = render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={10} budget={10} />);
  expect(container.querySelector(".spend-red")).toBeTruthy();
});
```

```tsx
// dashboard/test/AgentsSheet.test.tsx
import { render, screen, waitFor } from "@testing-library/preact";
import { AgentsSheet } from "../src/project/AgentsSheet";
import * as client from "../src/api/client";

test("lists each agent's roles, current work, runs today, and spend", async () => {
  jest.spyOn(client, "api").mockResolvedValue({ agents: [
    { name: "claude", roles: ["task", "subtask"], current: { node_id: 5, title: "Detect pitch", elapsed_s: 90 }, runs_today: 3, spend: 1.2, budget: 10, last_error: null },
    { name: "codex", roles: ["spec"], current: null, runs_today: 0, spend: 0, budget: 10, last_error: "rate limited" },
  ] });
  render(<AgentsSheet projectId={1} onClose={() => {}} />);
  await waitFor(() => screen.getByText("claude"));
  expect(screen.getByText(/Detect pitch/)).toBeTruthy();
  expect(screen.getByText("idle")).toBeTruthy();
  expect(screen.getByText("rate limited")).toBeTruthy();
});
```

```tsx
// dashboard/test/ProjectPage.test.tsx
import { render, screen, waitFor } from "@testing-library/preact";
import { ProjectPage } from "../src/project/ProjectPage";
import * as client from "../src/api/client";
import { projectId, projects } from "../src/state";

test("renders Canvas on desktop width using merged /graph and /nodes data", async () => {
  projects.value = [{ id: 1, goal: "Voxscore", phase: "planning" }];
  projectId.value = 1;
  jest.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, agent: null }], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null }]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Voxscore"));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npx vitest run test/StatusLine.test.tsx test/AgentsSheet.test.tsx test/ProjectPage.test.tsx`
Expected: FAIL — modules not found

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/project/StatusLine.tsx
export function StatusLine({ phase, done, total, busyAgents, spend, budget }: { phase: string; done: number; total: number; busyAgents: number; spend: number; budget: number }) {
  const pct = budget > 0 ? spend / budget : 0;
  const spendClass = pct >= 1 ? "spend-red" : pct >= 0.8 ? "spend-amber" : "";
  return (
    <div class="status-line caption">
      {phase} · {done}/{total} done · {busyAgents} agents busy · <span class={spendClass}>${spend.toFixed(2)} of ${budget.toFixed(2)}</span>
    </div>
  );
}
```

```css
/* append to dashboard/src/canvas/canvas.css */
.status-line { padding: 4px 16px; }
.spend-amber { color: var(--tier-medium); }
.spend-red { color: var(--danger); }
```

```tsx
// dashboard/src/project/AgentsSheet.tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { Sheet } from "../ui/Sheet";

type AgentStatus = { name: string; roles: string[]; current: { node_id: number; title: string; elapsed_s: number } | null; runs_today: number; spend: number; budget: number; last_error: string | null };

export function AgentsSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  useEffect(() => { api<{ agents: AgentStatus[] }>(routes.agentsStatus(projectId)).then((r) => setAgents(r.agents)); }, [projectId]);
  return (
    <Sheet title="Agents" onClose={onClose}>
      {!agents ? <p class="muted">loading…</p> : (
        <div class="list">
          {agents.map((a) => (
            <div class="list-row" style={{ cursor: "default" }}>
              <span class="grow">
                <span class="title">{a.name} <span class="caption">{a.roles.join(", ")}</span></span>
                <span class="caption" style={{ display: "block" }}>{a.current ? `#${a.current.node_id} ${a.current.title} · ${a.current.elapsed_s}s` : "idle"}</span>
                <span class="caption" style={{ display: "block" }}>{a.runs_today} runs today · ${a.spend.toFixed(2)} of ${a.budget.toFixed(2)}</span>
                {a.last_error ? <span class="caption danger" style={{ display: "block" }}>{a.last_error}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  );
}
```

```tsx
// dashboard/src/project/HistorySheet.tsx
import { Sheet } from "../ui/Sheet";
import { Timeline } from "../activity/Timeline";

export function HistorySheet({ onClose }: { onClose: () => void }) {
  return <Sheet title="History" onClose={onClose}><Timeline /></Sheet>;
}
```

```tsx
// dashboard/src/project/NewProject.tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { loadProjects, projectId, toastError } from "../state";
import { openProject } from "../router";
import { Button } from "../ui/Button";

export function NewProject() {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(e: Event) {
    e.preventDefault();
    if (!goal.trim()) return;
    setBusy(true);
    try {
      const r = await post<{ project: { id: number } }>(routes.projects(), { goal });
      await loadProjects();
      projectId.value = r.project.id;
      openProject(r.project.id);
    } catch (e) { toastError(e); } finally { setBusy(false); }
  }
  return (
    <div class="page">
      <form class="card stack" style={{ maxWidth: 480, margin: "10dvh auto" }} onSubmit={create}>
        <h1 class="page-title">Start a project</h1>
        <input placeholder="goal" value={goal} onInput={(e) => setGoal((e.target as HTMLInputElement).value)} />
        <div class="actions"><Button type="submit" variant="filled" disabled={busy}>Create</Button></div>
      </form>
    </div>
  );
}
```

```tsx
// dashboard/src/project/ProjectPage.tsx
import { useMemo, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { useApi } from "../hooks";
import { currentProject, projectId, projects, refreshTick, toast, toastError } from "../state";
import { buildCanvasData, type Graph, type FullNode } from "../canvas/canvasData";
import { Canvas } from "../canvas/Canvas";
import { PhoneFlow } from "../canvas/PhoneFlow";
import { CardRail } from "../cards/CardRail";
import { cardsFromInbox, type Inbox, type Revision, type NodesById } from "../cards/cardsFromInbox";
import { pendingBreakdowns, clearBreakdown, shouldClearBreakdown } from "../canvas/pending";
import { StatusLine } from "./StatusLine";
import { AgentsSheet } from "./AgentsSheet";
import { NewProject } from "./NewProject";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";

const isPhone = () => typeof window !== "undefined" && window.innerWidth < 900;

export function ProjectPage() {
  const [agentsSheet, setAgentsSheet] = useState(false);
  const p = currentProject.value;
  const pid = projectId.value;

  const graphQ = useApi<Graph>(() => api(routes.graph(pid)), [pid, refreshTick.value]);
  const nodesQ = useApi<FullNode[]>(() => api(routes.nodes(pid)), [pid, refreshTick.value]);
  const inboxQ = useApi<Inbox>(() => api(routes.inbox()), [refreshTick.value]);
  const revisionsQ = useApi<Revision[]>(() => (pid ? api(routes.revisions(pid)) : Promise.resolve([])), [pid, refreshTick.value]);
  const allNodesQ = useApi<FullNode[]>(() => api(routes.nodes(null)), [refreshTick.value]);

  const data = useMemo(() => (graphQ.data && nodesQ.data ? buildCanvasData(graphQ.data, nodesQ.data) : null), [graphQ.data, nodesQ.data]);

  // Clear breakdown ghosts once real children outnumber the set present
  // when the breakdown was launched (pending.ts's `shouldClearBreakdown`).
  useMemo(() => {
    if (!data) return;
    for (const [nodeId, pending] of Object.entries(pendingBreakdowns.value)) {
      const currentChildIds = new Set(data.tasks.filter((t) => String(t.id) !== nodeId).map((t) => t.id));
      if (shouldClearBreakdown(pending, currentChildIds)) clearBreakdown(Number(nodeId));
    }
  }, [data]);

  const nodesById: NodesById = useMemo(() => Object.fromEntries((allNodesQ.data ?? []).map((n) => [n.id, { title: n.title, project_id: n.project_id }])), [allNodesQ.data]);
  const cards = useMemo(() => {
    if (!inboxQ.data || !pid) return [];
    return cardsFromInbox(inboxQ.data, { spec: data?.spec ?? null, taskCount: data?.tasks.length ?? 0, projectPhase: p?.phase ?? "planning", revisions: revisionsQ.data ?? [], nodesById, projectId: pid })
      .filter((c) => c.projectId === null || c.projectId === pid);
  }, [inboxQ.data, data, p?.phase, revisionsQ.data, nodesById, pid]);
  const needsYou = new Set(cards.map((c) => c.nodeId).filter((id): id is number => id !== null));

  if (!projects.value.length) return <NewProject />;
  if (!p) return <NewProject />;

  const done = (nodesQ.data ?? []).filter((n) => n.status === "done").length;
  const total = (nodesQ.data ?? []).filter((n) => n.kind === "task").length;
  const busyAgents = new Set((nodesQ.data ?? []).filter((n) => n.status === "in_progress").map((n) => n.owner)).size;

  async function run() {
    if (!pid) return;
    try { await post(routes.projectRun(pid), {}); toast("run started"); } catch (e) { toastError(e); }
  }

  return (
    <div class="page-canvas">
      <div class="row between" style={{ padding: "16px 16px 0" }}>
        <h1 class="page-title">{p.goal}</h1>
        <div class="row">
          <Button variant="filled" onClick={run}>▶ Run</Button>
        </div>
      </div>
      <button type="button" class="status-line-btn" onClick={() => setAgentsSheet(true)}>
        <StatusLine phase={p.phase} done={done} total={total} busyAgents={busyAgents} spend={0} budget={1} />
      </button>
      {graphQ.error || nodesQ.error ? <div class="callout danger">{graphQ.error || nodesQ.error}</div> : !data ? <Empty text="loading…" /> : (
        <div class="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
          <div style={{ flex: 1, minWidth: 0, padding: "0 16px 16px" }}>
            {isPhone() ? <PhoneFlow data={data} projectId={pid!} projectPhase={p.phase} needsYou={needsYou} /> : <Canvas data={data} projectId={pid!} projectPhase={p.phase} needsYou={needsYou} />}
          </div>
          <CardRail cards={cards} />
        </div>
      )}
      {agentsSheet ? <AgentsSheet projectId={pid!} onClose={() => setAgentsSheet(false)} /> : null}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npx vitest run test/StatusLine.test.tsx test/AgentsSheet.test.tsx test/ProjectPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Add Agents/History entries to `ProjectMenu.tsx`**

```tsx
// dashboard/src/shell/ProjectMenu.tsx — add inside the authed actions block, alongside Pause/Resume/Close
import { AgentsSheet } from "../project/AgentsSheet";
import { HistorySheet } from "../project/HistorySheet";
// ... add state: const [agents, setAgents] = useState(false); const [history, setHistory] = useState(false);
// ... add before the closing/pause-confirm early returns:
if (agents && p) return <AgentsSheet projectId={p.id} onClose={() => { setAgents(false); onClose(); }} />;
if (history) return <HistorySheet onClose={() => { setHistory(false); onClose(); }} />;
// ... add buttons in the actions row: <Button onClick={() => setAgents(true)}>Agents</Button><Button onClick={() => setHistory(true)}>History</Button>
```

- [ ] **Step 6: Run the ProjectMenu test suite to confirm no regression**

Run: `cd dashboard && npx vitest run test/shell.test.tsx`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd dashboard && git add src/project/ src/shell/ProjectMenu.tsx test/StatusLine.test.tsx test/AgentsSheet.test.tsx test/ProjectPage.test.tsx
git commit -m "Project: ProjectPage, StatusLine, NewProject, AgentsSheet, HistorySheet"
```

---

### Task 15: Wire the app together — routing, redirects, deletions

**Files:**
- Modify: `dashboard/src/app.tsx`, `dashboard/src/main.tsx`, `dashboard/src/shell/Sidebar.tsx`, `dashboard/src/shell/TabBar.tsx`, `dashboard/src/shell/TopBar.tsx`, `dashboard/src/state.ts`
- Delete: `dashboard/src/plan/`, `dashboard/src/inbox/`, `dashboard/src/activity/ActivityPage.tsx`, `dashboard/src/spend/`, `dashboard/src/spec/`, and `dashboard/test/PlanPage.test.tsx`, `SpecCard.test.tsx`, `SpecPage.test.tsx`, `SpendPage.test.tsx`, `InboxPage.test.tsx` (each superseded by an already-written test from Tasks 1–14; `Timeline.tsx` and its test move to stay reachable from `HistorySheet`, so `dashboard/src/activity/Timeline.tsx` and `dashboard/test/Timeline.test.tsx` are the one pair in this directory that is kept, not deleted).
- Test: `dashboard/test/router.test.ts` (extend), `dashboard/test/app.test.tsx` (new)

**Interfaces:**
- Consumes: `ProjectPage` (Task 14), `isLegacyRoute` (Task 4), everything else already built.
- Produces: the assembled app; nothing later consumes this task.

- [ ] **Step 1: Write the failing tests**

```tsx
// dashboard/test/app.test.tsx
import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";
import { route } from "../src/router";
import { projects, projectId } from "../src/state";

test("an old #/plan hash redirects to the current project's canvas", async () => {
  projects.value = [{ id: 4, goal: "g", phase: "planning" }];
  projectId.value = 4;
  window.location.hash = "#/plan";
  route.value = { page: "plan", params: [], query: new URLSearchParams() };
  render(<App />);
  await new Promise((r) => setTimeout(r, 0));
  expect(window.location.hash).toBe("#/p/4");
});

test("#/p/:id with no project selected still renders without throwing", () => {
  projects.value = [];
  projectId.value = null;
  window.location.hash = "#/";
  route.value = { page: "", params: [], query: new URLSearchParams() };
  render(<App />);
  expect(screen.getByText("Start a project")).toBeTruthy();
});
```

```typescript
// dashboard/test/router.test.ts — append
test("isLegacyRoute matches the retired tab pages and the old spec route", () => {
  expect(isLegacyRoute({ page: "plan", params: [], query: new URLSearchParams() })).toBe(true);
  expect(isLegacyRoute({ page: "inbox", params: [], query: new URLSearchParams() })).toBe(true);
  expect(isLegacyRoute({ page: "spec", params: ["3"], query: new URLSearchParams() })).toBe(true);
  expect(isLegacyRoute({ page: "p", params: ["1"], query: new URLSearchParams() })).toBe(false);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npx vitest run test/app.test.tsx test/router.test.ts`
Expected: FAIL — `App` still renders the old tab pages; `isLegacyRoute` import breaks nothing yet but the app-level redirect doesn't exist

- [ ] **Step 3: Write the implementation**

```tsx
// dashboard/src/app.tsx — replace the whole file
import { useEffect, useState } from "preact/hooks";
import { route, navigate, isLegacyRoute } from "./router";
import { authed, projectId } from "./state";
import { Sidebar } from "./shell/Sidebar";
import { TopBar } from "./shell/TopBar";
import { TokenBanner } from "./shell/TokenBanner";
import { Toasts } from "./ui/Toasts";
import { ProjectPage } from "./project/ProjectPage";
import { NodeSheet } from "./node/NodeSheet";
import { CommandPalette } from "./shell/CommandPalette";

export function App() {
  const [palette, setPalette] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (isLegacyRoute(route.value) && projectId.value !== null) navigate("#/p/" + projectId.value);
  }, [route.value.page, projectId.value]);
  const nodeId = route.value.query.get("node");
  return (
    <div class="app">
      <Sidebar />
      <div class="stack" style={{ flex: 1, minWidth: 0, gap: 0 }}>
        <TopBar onSearch={() => setPalette(true)} />
        {authed.value ? null : <TokenBanner />}
        <main><ProjectPage /></main>
      </div>
      {nodeId ? <NodeSheet key={nodeId} /> : null}
      {palette ? <CommandPalette onClose={() => setPalette(false)} /> : null}
      <Toasts />
    </div>
  );
}
```

```typescript
// dashboard/src/main.tsx — replace the whole file
import "./styles/tokens.css";
import "./styles/base.css";
import "./ui/ui.css";
import "./shell/shell.css";
import "./canvas/canvas.css";
import "./cards/cards.css";
import { render } from "preact";
import { App } from "./app";
import { exchangeFragmentNonce } from "./api/auth";
import { onForbidden, api } from "./api/client";
import { routes } from "./api/routes";
import { connectStream } from "./api/stream";
import { resetRouteFromLocation } from "./router";
import { setAuthed, loadProjects, protocolVersion, refreshTick, toastError } from "./state";
import { effect } from "@preact/signals";

onForbidden(() => setAuthed(false));

async function boot() {
  setAuthed(await exchangeFragmentNonce());
  resetRouteFromLocation();
  const h = await api<{ protocol_version: number }>(routes.healthz());
  protocolVersion.value = h.protocol_version;
  effect(() => {
    void refreshTick.value;
    loadProjects().catch(toastError);
  });
  connectStream();
  render(<App />, document.getElementById("root")!);
}

boot().catch((e) => {
  document.getElementById("root")!.textContent = "muvue dashboard failed to start: " + (e instanceof Error ? e.message : String(e));
});
```

```tsx
// dashboard/src/shell/Sidebar.tsx — replace the whole file (the nav list of tabs is gone; it's now a project switcher)
import { useState } from "preact/hooks";
import { projects, projectId, currentProject, authed, protocolVersion, refresh } from "../state";
import { openProject } from "../router";
import { Icon } from "../ui/Icon";
import { Pill } from "../ui/Pill";
import { ProjectMenu } from "./ProjectMenu";

export function Sidebar() {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <aside class="sidebar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow"><span class="project-goal">{p ? p.goal : "no project"}</span>{p ? <Pill status={p.phase} /> : null}</span>
        <Icon name="more" />
      </button>
      <nav>
        {projects.value.map((pr) => (
          <button type="button" class={"nav-item" + (pr.id === projectId.value ? " on" : "")} onClick={() => { projectId.value = pr.id; refresh(); openProject(pr.id); }}>
            <span class="dot" style={{ background: pr.phase === "closed" ? "var(--st-done)" : "var(--st-ready)" }} />
            <span class="grow">{pr.goal}</span>
          </button>
        ))}
        <button type="button" class="nav-item" onClick={() => openProject(0)}><Icon name="plan" /><span class="grow">+ New project</span></button>
      </nav>
      <div class="sidebar-foot caption">
        <div>{authed.value ? "signed in" : "read-only"}</div>
        {protocolVersion.value !== null ? <div>protocol v{protocolVersion.value}</div> : null}
      </div>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </aside>
  );
}
```

```tsx
// dashboard/src/shell/TopBar.tsx — unchanged in shape; drop the ProjectMenu's Plan/Inbox/Activity/Spend affordance if any existed (it didn't — TopBar already only opened ProjectMenu and search, no edit needed here beyond confirming it still compiles after Sidebar's NAV export removal)
```

Delete `dashboard/src/shell/TabBar.tsx` and its import from `app.tsx` (already removed above) since the phone bottom bar was tab navigation across the four retired pages; the phone layout now uses the project switcher (opened the same way as `ProjectMenu`, via the top bar's project button) instead of a tab bar. Remove `.tabbar`/`.tab`/`.tab-icon`/`.tab-label` rules' *usage* is unaffected (CSS can stay; unused rules are harmless, but delete them from `shell.css` for cleanliness since this plan's Global Constraints don't forbid a small cleanup here and dead CSS is exactly the kind of thing a fresh reviewer would flag).

```bash
cd dashboard && rm -rf src/plan src/inbox src/spend src/spec src/activity/ActivityPage.tsx \
  test/PlanPage.test.tsx test/SpecCard.test.tsx test/SpecPage.test.tsx test/SpendPage.test.tsx test/InboxPage.test.tsx \
  src/shell/TabBar.tsx
```

Move `Timeline.tsx` to stay under `activity/` (only `ActivityPage.tsx` is deleted, not the whole directory) — no file move needed, `dashboard/src/activity/Timeline.tsx` and `dashboard/test/Timeline.test.tsx` are simply left in place.

- [ ] **Step 4: Fix remaining references**

```bash
cd dashboard && grep -rl "PAGES\[" src test || true
grep -rl "from \"../plan\|from \"../inbox\|from \"../spend\|from \"../spec\"" src test || true
```

Fix each hit this turns up (there should be none left after Step 3's edits to `app.tsx`; if the grep finds any, it means a file from an earlier task imported one of the deleted modules directly instead of going through `ProjectPage`/`CardRail` — read that file and redirect the import to the Task 1–14 replacement it actually needs).

- [ ] **Step 5: Run the full dashboard test suite**

Run: `cd dashboard && npm test`
Expected: PASS — every test file from Tasks 1–15, plus the surviving pre-existing tests (`client.test.ts`, `diff.test.ts`, `shell.test.tsx`, `ui.test.tsx`, `smoke.test.tsx`, `Timeline.test.tsx`, `touch-targets.test.ts`, `no-stray-fetch.test.ts`). If `smoke.test.tsx` or `shell.test.tsx` reference a deleted page, update them to render `<App />` against `ProjectPage` instead (read the current file first — it likely just renders `<App/>` and checks it doesn't throw, which needs no change).

- [ ] **Step 6: Build and check the committed bundle stays fresh**

```bash
cd dashboard && npm run build
git -C .. diff --stat -- src/muvue/api/static/index.html
```

Expected: the build succeeds and `src/muvue/api/static/index.html` shows as changed (it must be committed in this task, per the CI job "The committed page must be the build of this source").

- [ ] **Step 7: Run the Python static/route tests**

Run: `cd .. && uv run pytest -q tests/test_dashboard_static.py`
Expected: PASS — `test_every_route_in_routes_ts_is_served` passes because every new path in `routes.ts` (Task 4) matches a real route in `src/muvue/api/app.py` (all pre-existing, from the merged backend plan).

- [ ] **Step 8: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: wire the canvas as the app's only page; retire Plan/Inbox/Activity/Spend/Spec tabs"
```

---

### Task 16: Docs and changelog

**Files:**
- Modify: `CHANGELOG.md`, `docs/decisions.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a CHANGELOG entry**

```markdown
<!-- CHANGELOG.md, under a new [Unreleased] section -->
## [Unreleased]

### Changed
- The dashboard is rebuilt around a single per-project canvas: a flow
  diagram of the spec and its tasks/subtasks (columns by `deps` order,
  labelled arrows, nested subtask rows), agent chips on every box, a
  notification-card rail replacing the Inbox tab, and creation/agent-
  breakdown directly from the page. The Plan, Inbox, Activity and Spend
  tabs, and the separate spec page, are retired — their content now
  lives in the canvas and the node sheet (`#/p/:id` replaces the old
  per-tab hashes, which redirect). No CLI verb loses its dashboard
  equivalent: `#/spec/:id` still opens that node's sheet, and History/
  Agents move into the project menu as sheets.
```

- [ ] **Step 2: Add a decisions.md entry**

```markdown
<!-- docs/decisions.md, next numbered entry (check the current highest number first: `grep -oE '^#### [0-9]+' docs/decisions.md | sort -n | tail -1`, and use the next integer) -->
#### N. The canvas replaces the dashboard's tabs; two card kinds are synthesized, not inbox-sourced

Context: `docs/superpowers/specs/2026-09-27-project-canvas-design.md` says
notification cards "come from the existing `/inbox` data, with no new
data source," listing "Spec awaiting approval" and "Task list awaiting
Gate 2" among them. `GET /inbox`'s actual response
(`src/muvue/api/app.py`) has no such categories — it returns
`questions`/`review`/`unverified_external`/`structure_updates`/`blocked`/
`awaiting_approval`/`signals`/`audit_items`/`unattributed_commits` only.

Decision: `dashboard/src/cards/cardsFromInbox.ts` synthesizes
`spec_review` and `gate2_review` cards client-side from the spec node's
own `status` and the project's `phase` — the same client-side rule the
pre-canvas dashboard's `SpecCard.tsx` already used for its Approve
buttons — rather than adding a new backend inbox category for two
conditions the frontend can already derive from data it fetches anyway.
"Plan revision proposed" cards are synthesized the same way from
`GET /projects/{id}/revisions`' unapproved rows.

Cost if wrong: if a future backend change makes spec/gate2/revision
state harder to derive from already-fetched data (for example, gate2
eligibility gaining a rule not visible in `phase`/`status`), this
synthesis silently drifts from the real rule and the card stops
appearing at the right time. Low probability: this mirrors an already-
shipped, already-tested client-side rule.
```

- [ ] **Step 3: Run the full Python suite once more, and the full dashboard suite once more, to confirm the doc-only changes didn't break anything mechanical**

Run: `uv run pytest -q && (cd dashboard && npm test)`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/decisions.md
git commit -m "Docs: canvas frontend changelog entry and the synthesized-cards decision"
```

---

## Self-Review Notes (for whoever executes this plan)

- **Spec coverage:** Screen layout (Tasks 8/9/14), The canvas structure/layout/states (Tasks 1/2/6/7/8/9), Creating from the page (Task 10), Agent breakdown (Task 10, `Actions.tsx` in Task 13), Agent transparency (Task 5, Task 14's `AgentsSheet`, Task 13's `Runs`), Notification cards (Tasks 11/12), Node sheet (Task 13), Frontend structure (Tasks 1–15 collectively), Error handling (every task's inline error/Retry patterns, matching the existing `toastError`/`callout danger` convention throughout), Testing/Vitest (every task's own test file). **Not covered by this plan, deliberately:** the spec's "Testing → Live (Playwright...)" acceptance walkthrough — this plan's Vitest coverage plus the existing Python endpoint tests (already shipped with the backend plan) are the automated gate; a live Playwright pass is a manual verification step to run once this plan's tasks are all merged, using the `verify` skill against a scratch repo exactly as the backend plan's own live checks did, not a task with its own commit.
- **Corrections made while writing this plan** (see Task 11's note in full): the design spec's claim that all notification cards are inbox-sourced with "no new data source" doesn't match the real `/inbox` response; Task 11 synthesizes the two gate cards and the revision card from already-fetched project state instead, and Task 16 records this as a decision.
- **Type consistency check:** `CanvasTask`/`CanvasSpec`/`CanvasEdge` (Task 1) are the same shapes threaded through `flowLayout` (Task 2), `TaskBox`/`SpecRoot` (Tasks 6–7), `Canvas`/`PhoneFlow` (Tasks 8–9), and `ProjectPage` (Task 14) — no renamed fields between tasks. `Card`/`CardKind` (Task 11) match exactly between `cardsFromInbox`, `Card.tsx`, and `CardRail.tsx` (Task 12).
