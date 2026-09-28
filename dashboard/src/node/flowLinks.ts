import type { Graph } from "../canvas/canvasData";

export type FlowLink = { id: number; title: string; carries: string | null };

export function flowLinks(graph: Graph, nodeId: number): { receivesFrom: FlowLink[]; sendsTo: FlowLink[] } {
  const titleById = new Map(graph.nodes.map((n) => [n.id, n.title]));
  const deps = graph.edges.filter((e) => e.kind === "dep");
  const link = (id: number, carries: string | null | undefined): FlowLink => ({ id, title: titleById.get(id) ?? "", carries: carries ?? null });
  const receivesFrom = deps
    .filter((e) => e.to === nodeId)
    .map((e) => link(e.from, e.carries))
    .sort((a, b) => a.id - b.id);
  const sendsTo = deps
    .filter((e) => e.from === nodeId)
    .map((e) => link(e.to, e.carries))
    .sort((a, b) => a.id - b.id);
  return { receivesFrom, sendsTo };
}
