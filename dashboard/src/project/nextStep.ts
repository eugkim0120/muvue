import type { NodeStatus, Phase } from "../state";

export type NextStepInput = {
  phase: Phase;
  spec: { id: number; status: NodeStatus } | null;
  taskCount: number;
  readyCount: number;
  workingCount: number;
  reviewCount: number;
  doneCount: number;
  planning: boolean;
};
export type NextStepId =
  | "closed"
  | "write_spec"
  | "approve_spec"
  | "planning"
  | "plan_tasks"
  | "approve_tasks"
  | "paused"
  | "running"
  | "review"
  | "run"
  | "all_done"
  | "stuck";
export type NextStep = { id: NextStepId; title: string; detail: string };

export function nextStep(i: NextStepInput): NextStep {
  if (i.phase === "closed") return { id: "closed", title: "Project closed", detail: "Nothing more to do here." };
  if (!i.spec) return { id: "write_spec", title: "Write the spec", detail: "Describe what to build, one requirement per line." };
  if (i.spec.status === "pending") {
    return { id: "approve_spec", title: "Approve the spec", detail: "Read it (tap the spec box), comment on any line, then approve so tasks can be planned." };
  }
  if (i.planning) {
    return { id: "planning", title: "Planning tasks…", detail: "An agent is splitting the spec into tasks. They appear in the diagram when it finishes." };
  }
  if (i.phase === "planning" && i.taskCount === 0) {
    return { id: "plan_tasks", title: "Plan the tasks", detail: "Let an agent propose tasks from the spec, or add them yourself." };
  }
  if (i.phase === "planning") {
    return {
      id: "approve_tasks",
      title: "Approve the task list",
      detail: `Check the ${i.taskCount} task${i.taskCount === 1 ? "" : "s"} in the diagram. Approving freezes their criteria and lets Run start them.`,
    };
  }
  if (i.phase === "paused") return { id: "paused", title: "Paused", detail: "Resume from the project menu to continue." };
  if (i.workingCount > 0) {
    return {
      id: "running",
      title: "Agents are working",
      detail: `${i.workingCount} task${i.workingCount === 1 ? " is" : "s are"} in progress. Watch the diagram or open a task for its log.`,
    };
  }
  if (i.reviewCount > 0) {
    return {
      id: "review",
      title: "Review finished work",
      detail: `${i.reviewCount} task${i.reviewCount === 1 ? " waits" : "s wait"} for your review in Needs you.`,
    };
  }
  if (i.readyCount > 0) {
    return {
      id: "run",
      title: "Run the tasks",
      detail: `${i.readyCount} task${i.readyCount === 1 ? " is" : "s are"} ready. Press ▶ Run tasks to start agents on them.`,
    };
  }
  if (i.taskCount > 0 && i.doneCount === i.taskCount) return { id: "all_done", title: "All tasks done", detail: "Close the project from the project menu." };
  return { id: "stuck", title: "Nothing can run", detail: "The remaining tasks are blocked or waiting on earlier tasks. Open a task to see why." };
}

// Mirrors the backend's `core.queries.run_block_reason`: Run is enabled
// whenever there's a positive ready count in the executing phase, regardless
// of whether some other task is in_progress or in review elsewhere in the
// project. (The frontend used to also block on any in_progress/review task
// anywhere in the project, which was stricter than the backend and could
// disable Run even when other tasks were ready to go.)
export function runBlockReason(i: NextStepInput): string | null {
  if (i.phase === "closed") return "The project is closed.";
  if (i.phase === "paused") return "The project is paused.";
  if (i.phase === "planning") return "Run starts after the task list is approved.";
  if (i.readyCount > 0) return null;
  return "Nothing is ready to run.";
}
