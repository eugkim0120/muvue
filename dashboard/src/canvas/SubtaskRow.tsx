import type { CanvasSubtask } from "./canvasData";
import { openNode } from "../router";

const GLYPH: Record<string, string> = { done: "✓", in_progress: "◐", review: "◆", pending: "○", ready: "○", blocked: "■", failed: "✕", awaiting_approval: "◆" };

export function SubtaskRow({ subtask, needsYou }: { subtask: CanvasSubtask; needsYou: boolean }) {
  return (
    <button type="button" class="subtask-row" aria-label={`${subtask.status.replace("_", " ")}: ${subtask.title}`} onClick={(e) => { e.stopPropagation(); openNode(subtask.id); }}>
      <span class="glyph" aria-hidden="true" style={{ color: `var(--st-${subtask.status})` }}>{GLYPH[subtask.status] ?? "○"}</span>
      <span class="grow title">{subtask.title}</span>
      {subtask.owner ? <span class="caption subtask-owner">{subtask.owner.replace(/^runner:/, "")}</span> : null}
      {needsYou ? <span class="needs-you-dot" title="Needs your attention" aria-label="needs you" /> : null}
    </button>
  );
}
