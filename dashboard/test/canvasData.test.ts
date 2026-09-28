// dashboard/test/canvasData.test.ts
import { buildCanvasData } from "../src/canvas/canvasData";

const gnode = (id: number, kind: string, parent_id: number | null, extra: Partial<any> = {}) => ({
  id, project_id: 1, parent_id, kind, title: "g" + id, status: "ready" as const, risk_tier: "low" as const, owner: null, agent: "claude", ...extra,
});
const fnode = (id: number, kind: string, parent_id: number | null, extra: Partial<any> = {}) => ({
  id, project_id: 1, parent_id, kind, title: "n" + id, status: "ready" as const, risk_tier: "low" as const, owner: null,
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
