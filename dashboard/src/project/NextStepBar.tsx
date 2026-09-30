import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, signInOpen, toast } from "../state";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import { BreakdownButton } from "../canvas/AddForm";
import { launches, LAUNCH_TIMEOUT_MS, type Activity } from "./activity";
import type { NextStep } from "./nextStep";

export function NextStepBar({
  step,
  authed,
  projectId,
  specId,
  activity,
  onAddTask,
}: {
  step: NextStep;
  authed: boolean;
  projectId: number;
  specId: number | null;
  activity: Activity | null;
  onAddTask: () => void;
}) {
  const approveSpecA = useAction();
  const approveTasksA = useAction();

  async function approveSpec() {
    if (specId === null) return;
    const ok = await approveSpecA.run(() => post(routes.nodeApprove(specId), { target: "spec" }));
    if (ok) { toast("Spec approved"); refresh(); }
  }
  async function approveTasks() {
    const ok = await approveTasksA.run(() => post(routes.nodeApprove(projectId), { target: "gate2" }));
    if (ok) { toast("Task list approved"); refresh(); }
  }

  return (
    <section class="next-step card" data-next-step={step.id}>
      <span class="caption">Next</span>
      <h2>{step.title}</h2>
      <p class="muted next-detail">{step.detail}</p>
      {authed ? (
        <>
          {step.id === "approve_spec" && specId !== null ? (
            <div class="actions">
              <Button variant="filled" busy={approveSpecA.busy} busyLabel="Approving…" onClick={approveSpec}>Approve spec</Button>
              {approveSpecA.error ? <div class="callout danger">{approveSpecA.error}</div> : null}
            </div>
          ) : null}
          {step.id === "plan_tasks" && specId !== null ? (
            <div class="actions">
              <BreakdownButton nodeId={specId} projectId={projectId} activity={activity} label="✨ Plan tasks with agent" />
              <Button
                variant="outline"
                disabled={launches.value.some((l) => l.kind === "breakdown" && l.nodeId === specId && l.projectId === projectId && Date.now() - l.at < LAUNCH_TIMEOUT_MS)}
                onClick={onAddTask}
              >
                + Add task myself
              </Button>
            </div>
          ) : null}
          {step.id === "approve_tasks" && specId !== null ? (
            <div class="actions">
              <Button variant="filled" busy={approveTasksA.busy} busyLabel="Approving…" onClick={approveTasks}>Approve task list</Button>
              {approveTasksA.error ? <div class="callout danger">{approveTasksA.error}</div> : null}
            </div>
          ) : null}
        </>
      ) : (
        <Button variant="plain" onClick={() => { signInOpen.value = true; }}>Sign in to act</Button>
      )}
    </section>
  );
}
