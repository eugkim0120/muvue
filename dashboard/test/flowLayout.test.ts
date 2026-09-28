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

test("a dep cycle does not hang layout and produces a defined column for every node", () => {
  const tasks = [task(1), task(2)];
  const edges: CanvasEdge[] = [{ from: 1, to: 2, carries: null }, { from: 2, to: 1, carries: null }];
  const lay = computeLayout(tasks, edges);
  expect(Number.isFinite(lay.pos[1]!.col)).toBe(true);
  expect(Number.isFinite(lay.pos[2]!.col)).toBe(true);
});

test("a cycle elsewhere does not corrupt the column of a non-cyclic ancestor feeding into it", () => {
  const tasks = [task(4), task(3), task(2), task(1)]; // 1 -> 2 -> 3, plus 3 <-> 4 cycle; order forces 2 mid-recursion when cycle hits
  const edges: CanvasEdge[] = [
    { from: 1, to: 2, carries: null }, { from: 2, to: 3, carries: null },
    { from: 3, to: 4, carries: null }, { from: 4, to: 3, carries: null },
  ];
  const lay = computeLayout(tasks, edges);
  expect(lay.pos[2]!.col).toBe(1); // 2's column must stay correct regardless of the cycle at 3/4
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
