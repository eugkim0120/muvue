import type { CanvasTask, CanvasEdge } from "./canvasData";

// Box heights are fixed by the layout and set as CSS heights on the boxes, so
// a wrong estimate can only clip a box's content, never overlap two boxes.
// Measured in headless Chromium against the built CSS at the worst case: a
// 2-line title, a 1-line purpose, and an agent chip whose label wraps to two
// lines ("claude · starts after the task list is approved") needs 151px;
// the placeholder with both create buttons (the agent one wraps) needs 214px.
export const NODE_W = 240, SPEC_H = 112, TASK_H_BASE = 152, SUBTASK_ROW_H = 44, MAX_SUBTASK_ROWS = 3, PLACEHOLDER_H = 216, PAD = 16, GAP_X = 24, GAP_Y = 72;
export const PLACEHOLDER_ID = -1;

export function taskHeight(subtaskCount: number): number {
  const rows = Math.min(subtaskCount, MAX_SUBTASK_ROWS) + (subtaskCount > MAX_SUBTASK_ROWS ? 1 : 0);
  return TASK_H_BASE + rows * SUBTASK_ROW_H;
}

export function labelWidth(text: string): number {
  return Math.min(200, Math.round(text.length * 6.8) + 20);
}

export function truncateLabel(text: string): string {
  return text.length > 26 ? text.slice(0, 25) + "…" : text;
}

export type Placed = { x: number; y: number; w: number; h: number; rank: number };
export type Arrow = { from: number; to: number; kind: "spec" | "dep" | "placeholder"; path: string; label: { x: number; y: number; text: string; full: string; w: number } | null };
export type Layout = { pos: Record<number, Placed>; arrows: Arrow[]; width: number; height: number };

function ranksOf(tasks: CanvasTask[], edges: CanvasEdge[]): Record<number, number> {
  const incoming: Record<number, number[]> = {};
  for (const t of tasks) incoming[t.id] = [];
  for (const e of edges) incoming[e.to]?.push(e.from);
  const rank: Record<number, number> = {};
  function rankOf(id: number, path: Set<number>): number {
    if (rank[id] !== undefined) return rank[id]!;
    if (path.has(id)) return 1; // a cycle is a data bug; don't hang on it
    path.add(id);
    let r = 1;
    for (const from of incoming[id] ?? []) r = Math.max(r, rankOf(from, path) + 1);
    path.delete(id);
    rank[id] = r;
    return r;
  }
  for (const t of tasks) rankOf(t.id, new Set());
  return rank;
}

function arrowPath(a: Placed, b: Placed): { x1: number; y1: number; x2: number; y2: number; path: string } {
  const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y - 6;
  const dy = Math.max(24, (y2 - y1) / 2);
  return { x1, y1, x2, y2, path: `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2}` };
}

export function computeLayout(spec: { id: number } | null, tasks: CanvasTask[], edges: CanvasEdge[], placeholder: boolean): Layout {
  const taskIds = new Set(tasks.map((t) => t.id));
  const depEdges = edges.filter((e) => taskIds.has(e.from) && taskIds.has(e.to));
  const taskRank = ranksOf(tasks, depEdges);
  const heightOf: Record<number, number> = {};
  const rows: number[][] = [];
  if (spec) { rows[0] = [spec.id]; heightOf[spec.id] = SPEC_H; }
  for (const t of tasks) { (rows[taskRank[t.id]!] ??= []).push(t.id); heightOf[t.id] = taskHeight(t.subtasks.length); }
  if (placeholder) { (rows[1] ??= []).push(PLACEHOLDER_ID); heightOf[PLACEHOLDER_ID] = PLACEHOLDER_H; }

  const preds: Record<number, number[]> = {};
  for (const e of depEdges) (preds[e.to] ??= []).push(e.from);
  const order: Record<number, number> = {};
  if (spec) order[spec.id] = 0;
  const ranks = rows.map((ids, r) => ({ r, ids: ids ?? [] })).filter((row) => row.ids.length > 0);
  for (const { ids } of ranks) {
    const mean = (id: number) => {
      const ps = (preds[id] ?? []).filter((p) => order[p] !== undefined).map((p) => order[p]!);
      if (!ps.length) return 0; // no placed predecessor: the spec (order 0) is its parent
      return ps.reduce((s, o) => s + o, 0) / ps.length;
    };
    ids.sort((a, b) => mean(a) - mean(b) || a - b);
    ids.forEach((id, i) => { order[id] = i; });
  }

  const rowWidth = (n: number) => n * NODE_W + (n - 1) * GAP_X;
  const inner = Math.max(0, ...ranks.map(({ ids }) => rowWidth(ids.length)));
  const pos: Record<number, Placed> = {};
  let y = PAD;
  let bottom = PAD;
  for (const { r, ids } of ranks) {
    const rowH = Math.max(...ids.map((id) => heightOf[id]!));
    const x0 = PAD + (inner - rowWidth(ids.length)) / 2;
    ids.forEach((id, i) => { pos[id] = { x: x0 + i * (NODE_W + GAP_X), y, w: NODE_W, h: heightOf[id]!, rank: r }; });
    bottom = y + rowH;
    y = bottom + GAP_Y;
  }

  const arrows: Arrow[] = [];
  if (spec) {
    for (const t of tasks) {
      if (taskRank[t.id] !== 1) continue;
      arrows.push({ from: spec.id, to: t.id, kind: "spec", path: arrowPath(pos[spec.id]!, pos[t.id]!).path, label: null });
    }
    if (placeholder) arrows.push({ from: spec.id, to: PLACEHOLDER_ID, kind: "placeholder", path: arrowPath(pos[spec.id]!, pos[PLACEHOLDER_ID]!).path, label: null });
  }
  for (const e of depEdges) {
    const { x1, y1, x2, y2, path } = arrowPath(pos[e.from]!, pos[e.to]!);
    const text = e.carries ? truncateLabel(e.carries) : null;
    const label = e.carries && text ? { x: (x1 + x2) / 2, y: (y1 + y2) / 2, text, full: e.carries, w: labelWidth(text) } : null;
    arrows.push({ from: e.from, to: e.to, kind: "dep", path, label });
  }
  return { pos, arrows, width: inner + 2 * PAD, height: bottom + PAD };
}
