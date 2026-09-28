import { activityItems, type Activity, type Launch, LAUNCH_TIMEOUT_MS } from "../src/project/activity";

const idle: Activity = { active: [], breakdowns: [], working: [] };
const titles = { 1: "voxscore v0.1" };

test("a fresh launch shows Starting… immediately", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, label: "planning", afterEventId: 0, at: 1000 };
  const items = activityItems(idle, [l], 1100, titles, new Set(), 1);
  expect(items).toEqual([expect.objectContaining({ tone: "busy", text: "Starting planning…" })]);
});

test("a running breakdown names the agent and the node", () => {
  const a: Activity = { active: [{ kind: "breakdown", pid: 5, node_id: 1, started_at: "t" }], breakdowns: [{ node_id: 1, event_id: 8, type: "breakdown.started", ts: "t", agent: "fake", reason: null, created: null }], working: [] };
  const [item] = activityItems(a, [], 0, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "busy", text: "✨ fake is planning tasks for “voxscore v0.1”", log: { kind: "node", nodeId: 1 } });
});

test("a failed breakdown stays on screen with its reason until dismissed", () => {
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "agent produced no parseable breakdown", created: null }], working: [] };
  expect(activityItems(a, [], 0, titles, new Set(), 1)).toEqual([expect.objectContaining({ tone: "error", text: "Planning “voxscore v0.1” failed: agent produced no parseable breakdown" })]);
  expect(activityItems(a, [], 0, titles, new Set(["bd-fail:9"]), 1)).toEqual([]);
});

test("a launch that the server confirmed is not shown twice", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, label: "planning", afterEventId: 7, at: 0 };
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "x", created: null }], working: [] };
  const items = activityItems(a, [l], 10, titles, new Set(), 1);
  expect(items.map((i) => i.tone)).toEqual(["error"]);
});

test("an unconfirmed launch turns into an error after the timeout", () => {
  const l: Launch = { kind: "run", nodeId: null, label: "the run", afterEventId: 0, at: 0 };
  const [item] = activityItems(idle, [l], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "error", text: "the run did not start: nothing was reported within 15 seconds. Check the log." });
});

test("a live run lists what is being worked on", () => {
  const a: Activity = { active: [{ kind: "run", pid: 3, node_id: null, started_at: "t" }], breakdowns: [], working: [{ node_id: 2, title: "Detect pitch", agent: "claude" }] };
  const [item] = activityItems(a, [], 0, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "busy", text: "▶ Running tasks · 1 agent working — Detect pitch", log: { kind: "project", projectId: 1 } });
});
