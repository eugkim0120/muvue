import type { NodeStatus, Phase, RiskTier } from "../state";

export function Pill({ status }: { status: NodeStatus | Phase }) {
  return <span class={"pill st-" + status}>{status.replace(/_/g, " ")}</span>;
}

export function Tier({ tier }: { tier: RiskTier }) {
  return <span class={"tier tier-" + tier}>{tier} risk</span>;
}
