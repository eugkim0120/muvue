import { agentStateOf } from "../src/canvas/agentState";

const node = (status: string, owner: string | null = null) => ({ status, owner, agent: "claude" } as const);

test("every state per the design's Agent chip states", () => {
  expect(agentStateOf(node("ready"), "planning", false)).toBe("queued");
  expect(agentStateOf(node("in_progress", "claude"), "planning", false)).toBe("running");
  expect(agentStateOf(node("review"), "planning", true)).toBe("waiting_on_you");
  expect(agentStateOf(node("awaiting_approval"), "planning", true)).toBe("waiting_on_you");
  expect(agentStateOf(node("done"), "planning", false)).toBe("done");
  expect(agentStateOf(node("failed"), "planning", false)).toBe("failed");
  expect(agentStateOf(node("in_progress"), "paused", false)).toBe("paused");
});

test("no routed or owning agent is unassigned regardless of status", () => {
  expect(agentStateOf({ status: "ready", owner: null, agent: null }, "planning", false)).toBe("unassigned");
});
