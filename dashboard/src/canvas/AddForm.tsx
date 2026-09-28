import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh } from "../state";
import type { CanvasTask } from "./canvasData";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import { markLaunched, type Activity } from "../project/activity";

type Receives = { id: number; carries: string };

export function AddForm({ parentId, kind, candidates, onClose }: { parentId: number; kind: "task" | "subtask"; candidates: CanvasTask[]; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [criteria, setCriteria] = useState("");
  const [receives, setReceives] = useState<Receives[]>([]);
  const a = useAction();

  function toggle(id: number) {
    setReceives((r) => (r.some((x) => x.id === id) ? r.filter((x) => x.id !== id) : [...r, { id, carries: "" }]));
  }
  function setCarries(id: number, carries: string) {
    setReceives((r) => r.map((x) => (x.id === id ? { ...x, carries } : x)));
  }

  async function save() {
    if (!title.trim()) return;
    const ok = await a.run(() => post(routes.nodeChildren(parentId), {
      title, body_md: purpose, criteria: criteria.split("\n").map((l) => l.trim()).filter(Boolean),
      depends_on: receives.map((r) => ({ id: r.id, carries: r.carries || null })), predicted_touches: [],
    }));
    if (ok) { refresh(); onClose(); }
  }

  return (
    <form class="card stack tight add-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <input placeholder="purpose" value={purpose} onInput={(e) => setPurpose((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one per line" value={criteria} onInput={(e) => setCriteria((e.target as HTMLTextAreaElement).value)} />
      {candidates.length ? (
        <div class="stack tight">
          <div class="caption">Receives from</div>
          {candidates.map((c) => {
            const picked = receives.find((r) => r.id === c.id);
            return (
              <div class="row">
                <label class="row" style={{ flex: 1, minHeight: "44px", alignItems: "center" }}>
                  <input type="checkbox" aria-label={`receives from ${c.title}`} style={{ width: "auto", minHeight: 0 }} checked={!!picked} onChange={() => toggle(c.id)} />
                  {c.title}
                </label>
                {picked ? <input placeholder="carrying ___" value={picked.carries} onInput={(e) => setCarries(c.id, (e.target as HTMLInputElement).value)} /> : null}
              </div>
            );
          })}
        </div>
      ) : null}
      {a.error ? <div class="callout danger">{a.error}</div> : null}
      <div class="actions"><Button type="submit" variant="filled" busy={a.busy} busyLabel="Saving…">Save</Button><Button variant="plain" onClick={onClose}>Cancel</Button></div>
    </form>
  );
}

export function BreakdownButton({ nodeId, disabled, caption, activity }: { nodeId: number; disabled?: boolean; caption?: string; activity: Activity | null }) {
  const a = useAction();
  async function run() {
    const ok = await a.run(async () => {
      await post(routes.nodeBreakdown(nodeId), {});
    });
    if (ok) { markLaunched({ kind: "breakdown", nodeId, label: "planning" }, activity); refresh(); }
  }
  return (
    <div class="stack tight">
      <Button variant="outline" disabled={disabled} busy={a.busy} busyLabel="Starting…" onClick={run}>✨ Break down with agent</Button>
      {disabled && caption ? <div class="caption">{caption}</div> : null}
      {a.error ? <div class="callout danger">{a.error}</div> : null}
    </div>
  );
}
