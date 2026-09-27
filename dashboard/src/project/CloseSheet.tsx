import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast, toastError } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";

type Preview = { closeable: boolean; blocking_nodes: number[]; diff: Record<string, { title?: string; name?: string }[]> };
type CloseResult = { fast_forwarded?: boolean; structure_ref?: string; pr_url?: string; pr_error?: string };
const SECTIONS: [string, string][] = [["decisions", "Decisions"], ["promoted_lessons", "Promoted lessons"], ["components", "Components"], ["changed_components", "Changed components"]];

export function CloseSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pr, setPr] = useState(false);
  useEffect(() => { api<Preview>(routes.closePreview(projectId)).then(setPreview, (e) => setError(e.message)); }, [projectId]);
  async function close() {
    try {
      const r = await post<{ result?: CloseResult } & CloseResult>(routes.projectClose(projectId), { pr });
      const res = r.result ?? r;
      toast(res.fast_forwarded ? "closed; main fast-forwarded" : `closed; structure is on ${res.structure_ref}${res.pr_url ? `, PR ${res.pr_url}` : res.pr_error ? ` (PR failed: ${res.pr_error})` : ""}`);
      refresh(); onClose();
    } catch (e) { toastError(e); }
  }
  return (
    <Sheet title={`Close project #${projectId}`} onClose={onClose}>
      {error ? <div class="callout danger">{error}</div> : !preview ? <p class="muted">loading…</p> : (
        <div class="stack">
          {!preview.closeable ? <div class="callout danger">Not closeable yet: tasks {preview.blocking_nodes.map((n) => "#" + n).join(", ")} are not done.</div> : null}
          {SECTIONS.map(([key, label]) => {
            const items = preview.diff[key] ?? [];
            return <section><h3>{label} <span class="caption">{items.length}</span></h3>{items.length ? <ul class="criteria">{items.map((i) => <li>{i.title ?? i.name ?? JSON.stringify(i)}</li>)}</ul> : <p class="muted">none</p>}</section>;
          })}
          <label class="row"><input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={pr} onChange={(e) => setPr((e.target as HTMLInputElement).checked)} /> open a GitHub PR if main can't be fast-forwarded</label>
          <div class="actions"><Button variant="danger" disabled={!preview.closeable} onClick={close}>Close and commit structure</Button><Button variant="plain" onClick={onClose}>Cancel</Button></div>
        </div>
      )}
    </Sheet>
  );
}
