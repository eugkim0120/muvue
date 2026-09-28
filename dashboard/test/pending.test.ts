import { pendingCreates, pendingBreakdowns, startCreate, resolveCreate, failCreate, startBreakdown, shouldClearBreakdown } from "../src/canvas/pending";

test("startCreate then resolveCreate clears the ghost", () => {
  startCreate("task:1:new", { kind: "task", parentId: 1, title: "Do the thing" });
  expect(pendingCreates.value["task:1:new"]!.title).toBe("Do the thing");
  resolveCreate("task:1:new");
  expect(pendingCreates.value["task:1:new"]).toBeUndefined();
});

test("failCreate keeps the ghost with an error for Retry", () => {
  startCreate("task:1:new", { kind: "task", parentId: 1, title: "x" });
  failCreate("task:1:new", "network error");
  expect(pendingCreates.value["task:1:new"]!.error).toBe("network error");
});

test("shouldClearBreakdown is true once more children exist than at breakdown start", () => {
  startBreakdown(5, { nodeId: 5, agent: "claude", log: ".muvue/logs/breakdown-5.log", startChildIds: new Set([10, 11]) });
  expect(pendingBreakdowns.value[5]).toBeDefined();
  expect(shouldClearBreakdown(pendingBreakdowns.value[5]!, new Set([10, 11]))).toBe(false);
  expect(shouldClearBreakdown(pendingBreakdowns.value[5]!, new Set([10, 11, 12, 13, 14]))).toBe(true);
});
