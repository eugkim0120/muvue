import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { Logs } from "./Logs";

type Run = { id: number; ts: string; type: string; node_id: number; actor: string; payload: string };

export function Runs({ nodeId }: { nodeId: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { api<{ runs: Run[] }>(routes.nodeRuns(nodeId)).then((r) => setRuns(r.runs)); }, [nodeId]);
  if (!runs) return <p class="muted">loading…</p>;
  if (!runs.length) return <p class="muted">no runs yet</p>;
  return (
    <div class="list">
      {runs.map((r) => (
        <div class="list-row" style={{ cursor: "default" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
          <span class="grow">
            <span class="title">{r.type}</span>
            <span class="caption" style={{ display: "block" }}>{r.actor.replace(/^runner:/, "")} · {r.ts}</span>
          </span>
        </div>
      ))}
      {open !== null ? <Logs nodeId={nodeId} /> : null}
    </div>
  );
}
