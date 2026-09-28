import type { CanvasSubtask } from "./canvasData";
import { openNode } from "../router";

const GLYPH: Record<string, string> = { done: "✓", in_progress: "●", review: "●", pending: "○", ready: "○", blocked: "⚠", failed: "⚠", awaiting_approval: "●" };

export function SubtaskRow({ subtask, needsYou }: { subtask: CanvasSubtask; needsYou: boolean }) {
  return (
    <button type="button" class="subtask-row" onClick={(e) => { e.stopPropagation(); openNode(subtask.id); }}>
      <span class="glyph" style={{ color: `var(--st-${subtask.status})` }}>{GLYPH[subtask.status] ?? "○"}</span>
      <span class="grow title">{subtask.title}</span>
      {needsYou ? <span class="needs-you-dot" aria-label="needs you" /> : null}
    </button>
  );
}
