import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { Logs } from "./Logs";

type Run = { id: number; ts: string; type: string; node_id: number; actor: string; payload: string };

export function Runs({ nodeId }: { nodeId: number }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ runs: Run[] }>(routes.nodeRuns(nodeId)).then(
      (r) => { if (!alive) return; setRuns(r.runs); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [nodeId]);
  if (error) return <div class="callout danger">{error}</div>;
  if (!runs) return <p class="muted">loading…</p>;
  if (!runs.length) return <p class="muted">no runs yet</p>;
  return (
    <div class="list">
      {runs.map((r) => (
        <button type="button" class="list-row" style={{ cursor: "default" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
          <span class="grow">
            <span class="title">{r.type}</span>
            <span class="caption" style={{ display: "block" }}>{r.actor.replace(/^runner:/, "")} · {r.ts}</span>
          </span>
        </button>
      ))}
      {open !== null ? <Logs nodeId={nodeId} /> : null}
    </div>
  );
}
