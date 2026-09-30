import type { NodeRow, NodeStatus, RiskTier } from "../state";

export type GraphNode = NodeRow & { agent: string | null };
export type GraphEdge = { from: number; to: number; kind: "parent" | "dep"; carries?: string | null };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

export type FullNode = NodeRow & {
  body_md: string | null; criteria_json: string; criteria_hash: string | null; block_reason: string | null; deleted_at: string | null;
};

export type CanvasSubtask = { id: number; title: string; status: NodeStatus; parent_id: number; owner: string | null };
export type CanvasSpec = { id: number; title: string; body_md: string | null; status: NodeStatus; agent: string | null };
export type CanvasTask = {
  id: number; title: string; status: NodeStatus; risk_tier: RiskTier; owner: string | null; agent: string | null;
  body_md: string | null; criteria_hash: string | null; block_reason: string | null; subtasks: CanvasSubtask[];
};
export type CanvasEdge = { from: number; to: number; carries: string | null };
export type CanvasData = { spec: CanvasSpec | null; tasks: CanvasTask[]; edges: CanvasEdge[] };

// The purpose line: the first non-empty line of body_md, per "The canvas ->
// Task box" (this belongs on canvasData, not the component, since AddForm's
// ghost box needs the same rule before any body_md round-trips through the API).
export function purposeLine(bodyMd: string | null): string {
  return (bodyMd || "").split("\n").find((l) => l.trim())?.trim() ?? "";
}

export function buildCanvasData(graph: Graph, fullNodes: FullNode[]): CanvasData {
  const agentById = new Map(graph.nodes.map((n) => [n.id, n.agent]));
  const byId = new Map(fullNodes.map((n) => [n.id, n]));
  const specRow = fullNodes.find((n) => n.kind === "spec") ?? null;
  const spec: CanvasSpec | null = specRow
    ? { id: specRow.id, title: specRow.title, body_md: specRow.body_md, status: specRow.status, agent: agentById.get(specRow.id) ?? null }
    : null;
  const subtasksByParent = new Map<number, CanvasSubtask[]>();
  for (const n of fullNodes) {
    if (n.kind !== "subtask" || n.parent_id === null) continue;
    const list = subtasksByParent.get(n.parent_id) ?? [];
    list.push({ id: n.id, title: n.title, status: n.status, parent_id: n.parent_id, owner: n.owner });
    subtasksByParent.set(n.parent_id, list);
  }
  const tasks: CanvasTask[] = fullNodes
    .filter((n) => n.kind === "task")
    .map((n) => ({
      id: n.id, title: n.title, status: n.status, risk_tier: n.risk_tier, owner: n.owner,
      agent: agentById.get(n.id) ?? null, body_md: n.body_md, criteria_hash: n.criteria_hash,
      block_reason: n.block_reason, subtasks: (subtasksByParent.get(n.id) ?? []).sort((a, b) => a.id - b.id),
    }));
  const taskIds = new Set(tasks.map((t) => t.id));
  const edges: CanvasEdge[] = graph.edges
    .filter((e) => e.kind === "dep" && taskIds.has(e.from) && taskIds.has(e.to) && byId.has(e.from) && byId.has(e.to))
    .map((e) => ({ from: e.from, to: e.to, carries: e.carries ?? null }));
  return { spec, tasks, edges };
}

export function subtaskProgress(subtasks: CanvasSubtask[]): { done: number; total: number } {
  return { done: subtasks.filter((s) => s.status === "done").length, total: subtasks.length };
}
