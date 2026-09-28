import { useState } from "preact/hooks";
import { DiffView } from "./DiffView";
import { Logs } from "./Logs";

export function Details({ nodeId, predictedTouches }: { nodeId: number; predictedTouches: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Details</summary>
      {open ? (
        <div class="stack">
          <section><h3>Predicted touches</h3><pre>{predictedTouches.join("\n") || "none"}</pre></section>
          <section><h3>Diff</h3><DiffView nodeId={nodeId} /></section>
          <section><h3>Logs</h3><Logs nodeId={nodeId} /></section>
        </div>
      ) : null}
    </details>
  );
}
