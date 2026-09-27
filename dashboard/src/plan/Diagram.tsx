import { layout, BOX_W, BOX_H, type Graph } from "./layout";
import { openNode } from "../router";

export function Diagram({ graph }: { graph: Graph }) {
  const lay = layout(graph);
  return (
    <div class="dag-wrap">
      <svg class="dag" width={lay.width} height={lay.height} viewBox={`0 0 ${lay.width} ${lay.height}`} role="img" aria-label="plan diagram">
        {graph.edges.map((e) => {
          const a = lay.pos[e.from]!, b = lay.pos[e.to]!;
          const x1 = a.x + BOX_W / 2, y1 = a.y + BOX_H, x2 = b.x + BOX_W / 2, y2 = b.y, mid = (y1 + y2) / 2;
          return <path class={"edge " + e.kind} d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`} />;
        })}
        {graph.nodes.map((n) => {
          const p = lay.pos[n.id]!;
          const text = `#${n.id} ${n.title}`;
          return (
            <g class="node" transform={`translate(${p.x},${p.y})`} tabIndex={0} onClick={() => openNode(n.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(n.id); }}>
              <title>{`${text} (${n.status}, ${n.risk_tier} risk)`}</title>
              <rect width={BOX_W} height={BOX_H} rx={10} />
              <rect class="bar" width={4} height={BOX_H} rx={2} style={{ fill: `var(--st-${n.status})` }} />
              <circle cx={18} cy={18} r={5} style={{ fill: `var(--st-${n.status})` }} />
              <text x={30} y={22}>{text.length > 24 ? text.slice(0, 23) + "…" : text}</text>
              <text x={30} y={40} class="sub">{`${n.kind} · ${n.status.replace(/_/g, " ")} · ${n.risk_tier}`}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
