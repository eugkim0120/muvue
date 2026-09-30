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
