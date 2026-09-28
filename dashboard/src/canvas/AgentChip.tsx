import type { AgentState } from "./agentState";

const LABEL: Record<AgentState, string> = {
  queued: "queued", running: "running", waiting_on_you: "waiting on you", done: "done", failed: "failed",
  paused: "paused", unassigned: "unassigned",
};

export function AgentChip({ agent, state }: { agent: string | null; state: AgentState }) {
  return (
    <span class={"chip agent-chip st-" + state}>
      {state === "running" ? <span class="pulse" aria-hidden="true" /> : null}
      {agent ? `${agent} · ${LABEL[state]}` : LABEL[state]}
    </span>
  );
}
