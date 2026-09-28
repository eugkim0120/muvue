import { authed } from "../state";
import { BreakdownButton } from "./AddForm";
import { Button } from "../ui/Button";
import type { Activity } from "../project/activity";

// It sits inside the pannable canvas frame, so click and keydown must not
// bubble up to the frame's pointer handlers.
function stopBubble(e: { stopPropagation: () => void }): void {
  e.stopPropagation();
}

export function DagPlaceholder({ specId, planning, activity, onAddTask, style }: { specId: number; planning: boolean; activity: Activity | null; onAddTask: () => void; style?: Record<string, string | number> }) {
  return (
    <div class="task-box dag-empty" style={style} onClick={stopBubble} onKeyDown={stopBubble}>
      {planning ? (
        <>
          <div class="row"><span class="spinner" /><span class="title">Planning tasks…</span></div>
          <div class="caption">Tasks appear here when the agent finishes.</div>
          <div class="dag-skeleton" />
          <div class="dag-skeleton" />
          <div class="dag-skeleton" />
        </>
      ) : (
        <>
          <span class="title">No tasks yet</span>
          <div class="caption">Tasks the spec breaks into will appear here, with arrows for what each one hands to the next.</div>
          {authed.value ? (
            <>
              <BreakdownButton nodeId={specId} activity={activity} label="✨ Plan tasks with agent" />
              <Button variant="plain" onClick={() => onAddTask()}>+ Add task myself</Button>
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
