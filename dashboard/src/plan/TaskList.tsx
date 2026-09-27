import type { NodeRow, NodeStatus } from "../state";
import { NodeRowItem } from "../ui/NodeRowItem";
import { Tier } from "../ui/Pill";

export const STATUS_ORDER: NodeStatus[] = ["review", "awaiting_approval", "blocked", "in_progress", "ready", "pending", "done", "failed"];

export function TaskList({ nodes }: { nodes: NodeRow[] }) {
  const groups = STATUS_ORDER.map((s) => ({ status: s, items: nodes.filter((n) => n.status === s) })).filter((g) => g.items.length);
  return (
    <div class="stack">
      {groups.map((g) => (
        <section>
          <h3 class="group-head" style={{ color: `var(--st-${g.status})` }}>{g.status.replace(/_/g, " ")} · {g.items.length}</h3>
          <div class="list">
            {g.items.map((n) => <NodeRowItem node={n} right={<Tier tier={n.risk_tier} />} sub={n.block_reason ? "blocked: " + n.block_reason : undefined} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
