import { useState } from "preact/hooks";
import type { CanvasTask } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";
import { SubtaskRow } from "./SubtaskRow";
import { MAX_SUBTASK_ROWS } from "./flowLayout";
import { AddForm, BreakdownButton } from "./AddForm";
import { Button } from "../ui/Button";

export function TaskBox({ task, needsYou, projectPhase, style }: { task: CanvasTask; needsYou: Set<number>; projectPhase: string; style?: Record<string, string | number> }) {
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
      <div class="row between">
        <AgentChip agent={task.agent} state={state} />
        <div class="actions">
          <Button variant="plain" onClick={(e) => { e.stopPropagation(); setAddingSubtask((a) => !a); }}>+ Subtask</Button>
          <Button variant="plain" onClick={(e) => { e.stopPropagation(); setBreakingDown((b) => !b); }}>✨</Button>
        </div>
      </div>
      {addingSubtask ? (
        <div onClick={(e) => e.stopPropagation()}>
          <AddForm parentId={task.id} kind="subtask" candidates={[]} onClose={() => setAddingSubtask(false)} />
        </div>
      ) : null}
      {breakingDown ? (
        <div onClick={(e) => e.stopPropagation()}>
          <BreakdownButton nodeId={task.id} />
        </div>
      ) : null}
    </div>
  );
}
