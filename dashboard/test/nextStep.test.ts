import { nextStep, runBlockReason, type NextStepInput } from "../src/project/nextStep";

const base: NextStepInput = { phase: "planning", spec: { id: 1, status: "ready" }, taskCount: 0, readyCount: 0, workingCount: 0, reviewCount: 0, doneCount: 0, planning: false };

test.each([
  [{ spec: null }, "write_spec"],
  [{ spec: { id: 1, status: "pending" } }, "approve_spec"],
  [{ planning: true }, "planning"],
  [{}, "plan_tasks"],
  [{ taskCount: 3 }, "approve_tasks"],
  [{ phase: "executing", taskCount: 3, readyCount: 2 }, "run"],
  [{ phase: "executing", taskCount: 3, workingCount: 1, readyCount: 2 }, "running"],
  [{ phase: "executing", taskCount: 3, reviewCount: 1 }, "review"],
  [{ phase: "executing", taskCount: 3, doneCount: 3 }, "all_done"],
  [{ phase: "executing", taskCount: 3 }, "stuck"],
  [{ phase: "paused", taskCount: 3, readyCount: 3 }, "paused"],
  [{ phase: "closed" }, "closed"],
] as const)("%o → %s", (over, id) => {
  expect(nextStep({ ...base, ...(over as Partial<NextStepInput>) }).id).toBe(id);
});

test("Run is only enabled at the run step, and says why otherwise", () => {
  expect(runBlockReason(nextStep({ ...base, phase: "executing", taskCount: 1, readyCount: 1 }))).toBeNull();
  expect(runBlockReason(nextStep({ ...base, taskCount: 3 }))).toBe("Run starts after the task list is approved.");
});

test("detail counts are pluralised", () => {
  expect(nextStep({ ...base, taskCount: 1 }).detail).toBe("Check the 1 task in the diagram. Approving freezes their criteria and lets Run start them.");
});
