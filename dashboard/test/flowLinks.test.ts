import { flowLinks } from "../src/node/flowLinks";
import type { Graph } from "../src/canvas/canvasData";

const node = (id: number, title: string) => ({ id, project_id: 1, parent_id: null, kind: "task", title, status: "ready" as const, risk_tier: "low" as const, owner: null, agent: null });

test("dep edges become receives-from and sends-to links; parent edges are ignored", () => {
  const g: Graph = {
    nodes: [node(2, "Record voice"), node(3, "Detect pitch"), node(4, "Export")],
    edges: [{ from: 2, to: 3, kind: "dep", carries: "audio frames" }, { from: 3, to: 4, kind: "dep", carries: null }, { from: 1, to: 3, kind: "parent" }],
  };
  expect(flowLinks(g, 3)).toEqual({
    receivesFrom: [{ id: 2, title: "Record voice", carries: "audio frames" }],
    sendsTo: [{ id: 4, title: "Export", carries: null }],
  });
});
