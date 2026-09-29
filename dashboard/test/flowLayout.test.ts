import { computeLayout, taskHeight, labelWidth, truncateLabel, NODE_W, GAP_X, PLACEHOLDER_ID, TASK_H_BASE, SUBTASK_ROW_H, SUBTASK_LIST_EXTRA } from "../src/canvas/flowLayout";
import type { CanvasTask, CanvasEdge } from "../src/canvas/canvasData";

const task = (id: number, subtaskCount = 0): CanvasTask => ({
  id, title: "t" + id, status: "ready", risk_tier: "low", owner: null, agent: "claude",
  body_md: null, criteria_hash: null, block_reason: null,
  subtasks: Array.from({ length: subtaskCount }, (_, i) => ({ id: id * 100 + i, title: "s" + i, status: "ready" as const, parent_id: id })),
});
const spec = { id: 1 };
const overlaps = (a: { x: number; y: number; w: number; h: number }, b: typeof a) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("spec sits alone on top; a chain goes straight down with spec + dep arrows", () => {
  const edges: CanvasEdge[] = [{ from: 2, to: 3, carries: "audio frames" }, { from: 3, to: 4, carries: "notes" }];
  const lay = computeLayout(spec, [task(2), task(3), task(4)], edges, false);
  expect([1, 2, 3, 4].map((id) => lay.pos[id]!.rank)).toEqual([0, 1, 2, 3]);
  expect(lay.pos[2]!.y).toBeGreaterThan(lay.pos[1]!.y + lay.pos[1]!.h);
  expect(lay.arrows.map((a) => a.kind).sort()).toEqual(["dep", "dep", "spec"]);
  expect(lay.arrows.find((a) => a.kind === "dep" && a.from === 2)!.label!.text).toBe("audio frames");
});

test("parallel tasks share a row side by side and never overlap", () => {
  const lay = computeLayout(spec, [task(2, 5), task(3), task(4, 2)], [], false);
  const boxes = [1, 2, 3, 4].map((id) => lay.pos[id]!);
  expect(new Set([2, 3, 4].map((id) => lay.pos[id]!.y)).size).toBe(1);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
  expect(lay.pos[3]!.x - lay.pos[2]!.x).toBe(NODE_W + GAP_X);
  expect(lay.arrows.filter((a) => a.kind === "spec")).toHaveLength(3);
});

test("rows are centered and the layout width fits the widest row", () => {
  const lay = computeLayout(spec, [task(2), task(3)], [], false);
  const specCenter = lay.pos[1]!.x + NODE_W / 2;
  const rowCenter = (lay.pos[2]!.x + lay.pos[3]!.x + NODE_W) / 2;
  expect(specCenter).toBeCloseTo(rowCenter);
  expect(lay.width).toBe(16 * 2 + 2 * NODE_W + GAP_X);
});

test("with no tasks, a placeholder node hangs under the spec with a dashed arrow", () => {
  const lay = computeLayout(spec, [], [], true);
  expect(lay.pos[PLACEHOLDER_ID]!.rank).toBe(1);
  expect(lay.arrows).toEqual([expect.objectContaining({ from: 1, to: PLACEHOLDER_ID, kind: "placeholder" })]);
});

test("box height is fixed by subtask count, capped at 3 rows plus a +N more row", () => {
  expect(taskHeight(0)).toBe(TASK_H_BASE);
  expect(taskHeight(2)).toBe(TASK_H_BASE + SUBTASK_LIST_EXTRA + 2 * SUBTASK_ROW_H);
  expect(taskHeight(4)).toBe(taskHeight(10));
});

test("labels are sized to their text and long ones are truncated", () => {
  expect(labelWidth("notes")).toBeLessThan(labelWidth("audio frames"));
  expect(truncateLabel("a".repeat(40))).toHaveLength(26);
  expect(truncateLabel("short")).toBe("short");
});

test("a dependency cycle does not hang and still ranks every node", () => {
  const lay = computeLayout(spec, [task(2), task(3)], [{ from: 2, to: 3, carries: null }, { from: 3, to: 2, carries: null }], false);
  expect(Number.isFinite(lay.pos[2]!.rank)).toBe(true);
  expect(Number.isFinite(lay.pos[3]!.rank)).toBe(true);
});
