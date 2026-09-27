import type { NodeRow } from "../state";

export type Edge = { from: number; to: number; kind: "parent" | "dep" };
export type Graph = { nodes: NodeRow[]; edges: Edge[] };
export type Placed = { x: number; y: number; layer: number; i: number };

export const BOX_W = 200, BOX_H = 52, GAP_X = 28, GAP_Y = 64, PAD = 16;

// A node's layer is the longest path to it over parent and dependency
// edges, so a dependency always sits above what waits on it. Within a
// layer, nodes are ordered by the mean position of their predecessors,
// which keeps siblings together and cuts crossings.
export function layout(graph: Graph): { pos: Record<number, Placed>; width: number; height: number } {
  const incoming: Record<number, number[]> = {};
  for (const n of graph.nodes) incoming[n.id] = [];
  for (const e of graph.edges) incoming[e.to]?.push(e.from);
  const depth: Record<number, number> = {};
  function depthOf(id: number, seen: Set<number>): number {
    if (depth[id] !== undefined) return depth[id]!;
    if (seen.has(id)) return 0; // a cycle would be a data bug; don't hang on it
    seen.add(id);
    let d = 0;
    for (const from of incoming[id] ?? []) d = Math.max(d, depthOf(from, seen) + 1);
    depth[id] = d;
    return d;
  }
  for (const n of graph.nodes) depthOf(n.id, new Set());
  const layers: number[][] = [];
  for (const n of graph.nodes) (layers[depth[n.id]!] ??= []).push(n.id);
  const pos: Record<number, Placed> = {};
  layers.forEach((layer, y) => {
    const center = (id: number) => {
      const xs = (incoming[id] ?? []).filter((f) => pos[f]).map((f) => pos[f]!.i);
      return xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : id;
    };
    layer.sort((a, b) => center(a) - center(b) || a - b);
    layer.forEach((id, i) => { pos[id] = { i, layer: y, x: 0, y: 0 }; });
  });
  const widest = Math.max(1, ...layers.map((l) => l.length));
  const width = PAD * 2 + widest * BOX_W + (widest - 1) * GAP_X;
  layers.forEach((layer) => {
    const rowWidth = layer.length * BOX_W + (layer.length - 1) * GAP_X;
    const left = (width - rowWidth) / 2;
    layer.forEach((id, i) => {
      pos[id]!.x = left + i * (BOX_W + GAP_X);
      pos[id]!.y = PAD + pos[id]!.layer * (BOX_H + GAP_Y);
    });
  });
  return { pos, width, height: PAD * 2 + layers.length * BOX_H + Math.max(0, layers.length - 1) * GAP_Y };
}
