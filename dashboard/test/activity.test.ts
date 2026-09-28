import { activityItems, dismiss, dismissedKeys, launches, planningNodeIds, type Activity, type Launch, LAUNCH_TIMEOUT_MS } from "../src/project/activity";

const idle: Activity = { active: [], breakdowns: [], working: [] };
const titles = { 1: "voxscore v0.1" };

test("a fresh launch shows Starting… immediately", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, projectId: 1, label: "planning", afterEventId: 0, at: 1000 };
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
  expect(activityItems(a, [], 0, titles, new Set(["bd-fail:1:9:t"]), 1)).toEqual([]);
});

test("a dismissed-failure key is scoped by project id, not just event id, so it can't hide a different repo's failure", () => {
  // Two different `muvue serve` repos on the same port can assign the same
  // small integer event_id; a project-1 dismissal of "bd-fail:1:9:t" must
  // not also hide project 2's event_id-9 failure.
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "x", created: null }], working: [] };
  expect(activityItems(a, [], 0, titles, new Set(["bd-fail:1:9:t"]), 2)).toHaveLength(1);
  expect(activityItems(a, [], 0, titles, new Set(["bd-fail:2:9:t"]), 2)).toEqual([]);
});

test("a launch that the server confirmed is not shown twice", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, projectId: 1, label: "planning", afterEventId: 7, at: 0 };
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "x", created: null }], working: [] };
  const items = activityItems(a, [l], 10, titles, new Set(), 1);
  expect(items.map((i) => i.tone)).toEqual(["error"]);
});

test("an unconfirmed launch turns into an error after the timeout", () => {
  const l: Launch = { kind: "run", nodeId: null, projectId: 1, label: "the run", afterEventId: 0, at: 0 };
  const [item] = activityItems(idle, [l], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "error", text: "the run did not start: nothing was reported within 15 seconds. Check the log." });
});

test("a timed-out launch's error carries a log reference instead of null, so View log renders", () => {
  const runLaunch: Launch = { kind: "run", nodeId: null, projectId: 1, label: "the run", afterEventId: 0, at: 0 };
  const [projectItem] = activityItems(idle, [runLaunch], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(projectItem).toMatchObject({ tone: "error", log: { kind: "project", projectId: 1 } });

  const nodeRunLaunch: Launch = { kind: "run", nodeId: 5, projectId: 1, label: "#5", afterEventId: 0, at: 0 };
  const [nodeRunItem] = activityItems(idle, [nodeRunLaunch], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(nodeRunItem).toMatchObject({ tone: "error", log: { kind: "node", nodeId: 5 } });

  const breakdownLaunch: Launch = { kind: "breakdown", nodeId: 1, projectId: 1, label: "planning", afterEventId: 0, at: 0 };
  const [breakdownItem] = activityItems(idle, [breakdownLaunch], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(breakdownItem).toMatchObject({ tone: "error", log: { kind: "node", nodeId: 1 } });
});

test("a live run lists what is being worked on", () => {
  const a: Activity = { active: [{ kind: "run", pid: 3, node_id: null, started_at: "t" }], breakdowns: [], working: [{ node_id: 2, title: "Detect pitch", agent: "claude" }] };
  const [item] = activityItems(a, [], 0, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "busy", text: "▶ Running tasks · 1 agent working — Detect pitch", log: { kind: "project", projectId: 1 } });
});

test("a timed-out launch's error can be dismissed, the same way a failed breakdown can", () => {
  const l: Launch = { kind: "run", nodeId: null, projectId: 1, label: "the run", afterEventId: 0, at: 0 };
  const now = LAUNCH_TIMEOUT_MS + 1;
  const items = activityItems(idle, [l], now, titles, new Set(), 1);
  expect(items).toEqual([expect.objectContaining({ tone: "error", key: "launch-timeout:run:null:0" })]);
  expect(activityItems(idle, [l], now, titles, new Set(["launch-timeout:run:null:0"]), 1)).toEqual([]);
});

test("dismissing a timed-out launch's error also drops it from `launches`, so useActivity stops polling for it", () => {
  launches.value = [{ kind: "run", nodeId: null, projectId: 1, label: "the run", afterEventId: 0, at: 0 }];
  dismiss("launch-timeout:run:null:0");
  expect(launches.value).toEqual([]);
});

test("dismiss caps the stored dismissed-key set at 200, dropping the oldest first", () => {
  dismissedKeys.value = new Set();
  for (let i = 0; i < 205; i++) dismiss(`bd-fail:1:${i}:t`);
  expect(dismissedKeys.value.size).toBe(200);
  expect(dismissedKeys.value.has("bd-fail:1:0:t")).toBe(false);
  expect(dismissedKeys.value.has("bd-fail:1:204:t")).toBe(true);
});

test("a launch belonging to a different project never surfaces as this project's activity item", () => {
  const other: Launch = { kind: "breakdown", nodeId: 1, projectId: 2, label: "planning", afterEventId: 0, at: 1000 };
  expect(activityItems(idle, [other], 1100, titles, new Set(), 1)).toEqual([]);
  const otherTimedOut: Launch = { kind: "run", nodeId: null, projectId: 2, label: "the run", afterEventId: 0, at: 0 };
  expect(activityItems(idle, [otherTimedOut], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1)).toEqual([]);
});

test("a breakdown launch belonging to a different project never marks this project's node as planning", () => {
  const other: Launch = { kind: "breakdown", nodeId: 1, projectId: 2, label: "planning", afterEventId: 0, at: Date.now() };
  expect(planningNodeIds(idle, [other], 1).has(1)).toBe(false);
  expect(planningNodeIds(idle, [other], 2).has(1)).toBe(true);
});
