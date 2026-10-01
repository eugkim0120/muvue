import type { CanvasTask, CanvasEdge } from "./canvasData";
import { purposeLine } from "./canvasData";

// Heights come from the content, derived from the built CSS (15px/1.4 title =
// 21px a line; 13px/1.4 caption = 18px; 10px padding + 1px border a side;
// 4px flex gap). The title-line count is an estimate from its length (the box
// fits about 26 characters a line), so ui-check's "content fits" and "no dead
// space" checks verify it in a real browser: fix the estimate, never hand-tune
// one box.
export const NODE_W = 240, SPEC_H = 112, SUBTASK_LIST_EXTRA = 12, SUBTASK_ROW_H = 44, MAX_SUBTASK_ROWS = 3, PLACEHOLDER_H = 140, PAD = 16, GAP_X = 24, GAP_Y = 72;
const BOX_CHROME = 22, TITLE_LINE = 21, CAPTION_LINE = 18, FLEX_GAP = 4, PURPOSE_MARGIN = 8, SLACK = 4;
export const PLACEHOLDER_ID = -1;

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
  for (const t of tasks) { (rows[taskRank[t.id]!] ??= []).push(t.id); heightOf[t.id] = taskHeight({ title: t.title, hasPurpose: !!purposeLine(t.body_md), hasReason: t.status === "blocked", subtaskCount: t.subtasks.length, hasProgress: t.subtasks.length > 0 }); }
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
