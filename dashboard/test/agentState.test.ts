import { agentStateOf } from "../src/canvas/agentState";

test.each([
  [{ status: "ready", owner: null, agent: null }, "executing", false, "unassigned"],
  [{ status: "ready", owner: null, agent: "fake" }, "planning", false, "waiting_for_plan_approval"],
  [{ status: "pending", owner: null, agent: "fake" }, "executing", false, "waiting_on_earlier"],
  [{ status: "ready", owner: null, agent: "fake" }, "executing", false, "ready"],
  [{ status: "in_progress", owner: "fake", agent: "fake" }, "executing", false, "running"],
  [{ status: "review", owner: null, agent: "fake" }, "executing", false, "waiting_on_you"],
  [{ status: "ready", owner: null, agent: "fake" }, "paused", false, "paused"],
  [{ status: "done", owner: null, agent: "fake" }, "planning", false, "done"],
] as const)("%o in %s → %s", (node, phase, needsYou, expected) => {
  expect(agentStateOf(node, phase, needsYou)).toBe(expected);
});
