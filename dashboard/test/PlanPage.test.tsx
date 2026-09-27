import { buildTaskGraph } from "../src/plan/PlanPage";

const n = (id: number, kind: "spec" | "task") => ({ id, project_id: 1, parent_id: null, kind, title: "t" + id, status: "ready" as const, risk_tier: "low" as const, owner: null });

test("buildTaskGraph drops spec nodes and any edge touching one", () => {
  const graph = {
    nodes: [n(1, "spec"), n(2, "task"), n(3, "task")],
    edges: [
      { from: 1, to: 2, kind: "parent" as const },
      { from: 2, to: 3, kind: "dep" as const },
    ],
  };
  const taskGraph = buildTaskGraph(graph);
  expect(taskGraph.nodes.map((n) => n.id)).toEqual([2, 3]);
  expect(taskGraph.nodes.some((n) => n.kind === "spec")).toBe(false);
  expect(taskGraph.edges).toEqual([{ from: 2, to: 3, kind: "dep" }]);
  for (const e of taskGraph.edges) {
    const ids = taskGraph.nodes.map((n) => n.id);
    expect(ids).toContain(e.from);
    expect(ids).toContain(e.to);
  }
});
