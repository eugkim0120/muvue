import type { CanvasData } from "./canvasData";
import { stepsFromColumns } from "./flowLayout";
import { TaskBox } from "./TaskBox";
import { SpecRoot } from "./SpecRoot";
import type { Activity } from "../project/activity";

export function PhoneFlow({ data, projectId, projectPhase, needsYou, activity }: { data: CanvasData; projectId: number; projectPhase: string; needsYou: Set<number>; activity?: Activity | null }) {
  const steps = stepsFromColumns(data.tasks, data.edges);
  return (
    <div class="stack phone-flow">
      <SpecRoot spec={data.spec} projectId={projectId} taskCount={data.tasks.length} projectPhase={projectPhase} />
      {steps.map((step) => (
        <div class="phone-step stack tight">
          {step.parallel ? <div class="caption parallel-label">parallel</div> : null}
          <div class={step.parallel ? "phone-step-row" : "stack tight"}>
            {step.tasks.map((t) => <TaskBox task={t} needsYou={needsYou} projectPhase={projectPhase} activity={activity} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
