import { useState } from "preact/hooks";
import type { CanvasTask } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { authed } from "../state";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";
import { SubtaskRow } from "./SubtaskRow";
import { MAX_SUBTASK_ROWS } from "./flowLayout";
import { AddForm, BreakdownButton } from "./AddForm";
import { Button } from "../ui/Button";
import type { Activity } from "../project/activity";

// Any wrapper around a new entry point (AddForm/BreakdownButton toggles and
// their forms) must swallow both click AND keydown, or Enter/click still
// bubbles up to the box's own onClick/onKeyDown and opens the node sheet.
function stopBubble(e: { stopPropagation: () => void }): void {
  e.stopPropagation();
}

export function TaskBox({ task, needsYou, projectPhase, style, activity }: { task: CanvasTask; needsYou: Set<number>; projectPhase: string; style?: Record<string, string | number>; activity?: Activity | null }) {
  const [addingSubtask, setAddingSubtask] = useState(false);
  const [breakingDown, setBreakingDown] = useState(false);
  const shown = task.subtasks.slice(0, MAX_SUBTASK_ROWS);
  const more = task.subtasks.length - shown.length;
  const state = agentStateOf(task, projectPhase, needsYou.has(task.id));
  return (
    <div class={"task-box st-bar-" + task.status} style={style} tabIndex={0} onClick={() => openNode(task.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(task.id); }}>
      <div class="row between">
        <span class="title grow">{task.title}</span>
        {needsYou.has(task.id) ? <span class="needs-you-dot" aria-label="needs you" /> : null}
      </div>
      {task.body_md ? <div class="caption purpose">{purposeLine(task.body_md)}</div> : null}
      {shown.length ? (
        <div class="subtask-list">
          {shown.map((s) => <SubtaskRow subtask={s} needsYou={needsYou.has(s.id)} />)}
          {more > 0 ? <div class="caption more-row">+{more} more</div> : null}
        </div>
      ) : null}
      <AgentChip agent={task.agent} state={state} />
      {authed.value ? (
        <div class="row task-box-actions" onClick={stopBubble} onKeyDown={stopBubble}>
          <Button variant="plain" onClick={() => setAddingSubtask((a) => !a)}>+ Subtask</Button>
          <Button variant="plain" onClick={() => setBreakingDown((b) => !b)}>✨</Button>
        </div>
      ) : null}
      {authed.value && addingSubtask ? (
        <div onClick={stopBubble} onKeyDown={stopBubble}>
          <AddForm parentId={task.id} kind="subtask" candidates={[]} onClose={() => setAddingSubtask(false)} />
        </div>
      ) : null}
      {authed.value && breakingDown ? (
        <div onClick={stopBubble} onKeyDown={stopBubble}>
          <BreakdownButton nodeId={task.id} activity={activity ?? null} />
        </div>
      ) : null}
    </div>
  );
}
