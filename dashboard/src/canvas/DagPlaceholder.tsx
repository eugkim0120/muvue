// It sits inside the pannable canvas frame, so click and keydown must not
// bubble up to the frame's pointer handlers.
function stopBubble(e: { stopPropagation: () => void }): void {
  e.stopPropagation();
}

// The "no tasks yet" state owns no action button of its own -- NextStepBar's
// "✨ Plan tasks with agent" (and its "+ Add task myself") is the sole owner
// of that action when `nextStep().id === "plan_tasks"`, which is exactly
// this state. Two independent "plan tasks" controls on screen at once was a
// D8-class regression (final review, Important #4); this placeholder is
// caption-only so there's never a second one.
export function DagPlaceholder({ planning, style }: { planning: boolean; style?: Record<string, string | number> }) {
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
          <div class="caption">Tasks will appear here, with arrows showing what each one hands to the next. Start with "✨ Plan tasks with agent" under Next.</div>
        </>
      )}
    </div>
  );
}
