import { layout } from "../src/plan/layout";

const n = (id: number) => ({ id, project_id: 1, parent_id: null, kind: "task", title: "t" + id, status: "ready" as const, risk_tier: "low" as const, owner: null });

test("a dependency sits in the layer above what waits on it", () => {
  const lay = layout({ nodes: [n(1), n(2), n(3)], edges: [{ from: 1, to: 2, kind: "parent" }, { from: 2, to: 3, kind: "dep" }] });
  expect(lay.pos[1]!.layer).toBe(0);
  expect(lay.pos[2]!.layer).toBe(1);
  expect(lay.pos[3]!.layer).toBe(2);
  expect(lay.pos[3]!.y).toBeGreaterThan(lay.pos[2]!.y);
});

test("siblings keep id order and the widest layer sets the width", () => {
  const lay = layout({ nodes: [n(1), n(2), n(3)], edges: [{ from: 1, to: 2, kind: "parent" }, { from: 1, to: 3, kind: "parent" }] });
  expect(lay.pos[2]!.x).toBeLessThan(lay.pos[3]!.x);
  expect(lay.width).toBe(16 * 2 + 2 * 200 + 28);
});

test("a cycle does not hang", () => {
  const lay = layout({ nodes: [n(1), n(2)], edges: [{ from: 1, to: 2, kind: "dep" }, { from: 2, to: 1, kind: "dep" }] });
  expect(Object.keys(lay.pos)).toHaveLength(2);
});
