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
