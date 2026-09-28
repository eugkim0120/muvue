import type { CanvasTask } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";
import { SubtaskRow } from "./SubtaskRow";
import { MAX_SUBTASK_ROWS } from "./flowLayout";

export function TaskBox({ task, needsYou, projectPhase, style }: { task: CanvasTask; needsYou: Set<number>; projectPhase: string; style?: Record<string, string | number> }) {
  const shown = task.subtasks.slice(0, MAX_SUBTASK_ROWS);
  const more = task.subtasks.length - shown.length;
  const state = agentStateOf(task, projectPhase, needsYou.has(task.id));
  return (
    <div class={"task-box st-bar-" + task.status + (task.status === "in_progress" ? " running" : "")} style={style} tabIndex={0} onClick={() => openNode(task.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(task.id); }}>
      <div class="row between" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
        <span class="title grow clamp-2">{task.title}</span>
        {needsYou.has(task.id) ? <span class="needs-you-dot" aria-label="needs you" /> : null}
      </div>
      {task.body_md ? <div class="caption purpose clamp-1">{purposeLine(task.body_md)}</div> : null}
      {shown.length ? (
        <div class="subtask-list">
          {shown.map((s) => <SubtaskRow subtask={s} needsYou={needsYou.has(s.id)} />)}
          {more > 0 ? <div class="caption more-row">+{more} more</div> : null}
        </div>
      ) : null}
      <AgentChip agent={task.agent} state={state} />
    </div>
  );
}
