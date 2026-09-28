export type AgentState = "queued" | "running" | "waiting_on_you" | "done" | "failed" | "paused" | "unassigned";

export function agentStateOf(
  node: { status: string; owner: string | null; agent: string | null },
  projectPhase: string,
  needsYou: boolean,
): AgentState {
  if (!node.agent) return "unassigned";
  if (projectPhase === "paused" && (node.status === "in_progress" || node.status === "ready")) return "paused";
  if (node.status === "done") return "done";
  if (node.status === "failed") return "failed";
  if (needsYou || node.status === "review" || node.status === "awaiting_approval") return "waiting_on_you";
  if (node.status === "in_progress") return "running";
  return "queued";
}
