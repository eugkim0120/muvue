import type { AgentState } from "./agentState";

const LABEL: Record<AgentState, string> = {
  unassigned: "no agent",
  waiting_for_plan_approval: "starts after the task list is approved",
  waiting_on_earlier: "waits for earlier tasks",
  ready: "ready — starts on Run",
  running: "working now",
  waiting_on_you: "waiting on you",
  done: "done",
  failed: "failed",
  paused: "paused",
};

export function AgentChip({ agent, state }: { agent: string | null; state: AgentState }) {
  return (
    <span class={"chip agent-chip st-" + state}>
      {state === "running" ? <span class="pulse" aria-hidden="true" /> : null}
      {agent ? `${agent} · ${LABEL[state]}` : LABEL[state]}
    </span>
  );
}
