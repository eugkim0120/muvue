import type { AgentState } from "./agentState";

// Short on purpose: the box is 240px wide and the Next bar already explains
// the project-wide gate ("Approve the task list"), so each box only needs
// its own state in a few words (decision #174).
const LABEL: Record<AgentState, string> = {
  unassigned: "no agent",
  waiting_for_plan_approval: "waits for approval",
  waiting_on_earlier: "waits for earlier tasks",
  ready: "ready to run",
  running: "working now",
  waiting_on_you: "needs you",
  done: "done",
  failed: "failed",
  paused: "paused",
};

// A status line, not a control: tapping the box opens the node, so this must
// not borrow `.chip`, the bordered 44px filter-chip style.
export function AgentChip({ agent, state }: { agent: string | null; state: AgentState }) {
  const label = agent ? `${agent} · ${LABEL[state]}` : LABEL[state];
  return (
    <span class={"agent-tag st-" + state} title={label}>
      <span class={"agent-dot" + (state === "running" ? " pulse" : "")} aria-hidden="true" />
      <span class="agent-tag-text">{label}</span>
    </span>
  );
}
