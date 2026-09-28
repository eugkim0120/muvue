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

test("Run is enabled whenever the project is executing with a ready task, and says why otherwise", () => {
  expect(runBlockReason({ ...base, phase: "executing", taskCount: 1, readyCount: 1 })).toBeNull();
  expect(runBlockReason({ ...base, taskCount: 3 })).toBe("Run starts after the task list is approved.");
  expect(runBlockReason({ ...base, phase: "paused", taskCount: 3, readyCount: 3 })).toBe("The project is paused.");
  expect(runBlockReason({ ...base, phase: "closed" })).toBe("The project is closed.");
  expect(runBlockReason({ ...base, phase: "executing", taskCount: 3 })).toBe("Nothing is ready to run.");
});

test("Important #3: Run is not falsely blocked by an in_progress or review task elsewhere when another task is ready", () => {
  // The backend's `run_block_reason` (core/queries.py) only checks phase
  // and the ready count -- it never blocks Run just because some other
  // task is in_progress or in review. The frontend used to be stricter.
  expect(runBlockReason({ ...base, phase: "executing", taskCount: 2, workingCount: 1, readyCount: 1 })).toBeNull();
  expect(runBlockReason({ ...base, phase: "executing", taskCount: 2, reviewCount: 1, readyCount: 1 })).toBeNull();
});

test("detail counts are pluralised", () => {
  expect(nextStep({ ...base, taskCount: 1 }).detail).toBe("Check the 1 task in the diagram. Approving freezes their criteria and lets Run start them.");
});
