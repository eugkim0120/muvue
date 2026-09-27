import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, projectId, refreshTick, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";

type Rev = { n: number; approved_at: string | null; diff: unknown };

function KeyValues({ value }: { value: unknown }) {
  if (typeof value !== "object" || value === null) return <pre>{JSON.stringify(value)}</pre>;
  return (
    <dl class="kv">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <><dt>{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v, null, 1) : String(v)}</dd></>
      ))}
    </dl>
  );
}

export function Revisions() {
  const [revs, setRevs] = useState<Rev[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pid = projectId.value;
  useEffect(() => {
    if (pid === null) { setRevs([]); return; }
    api<Rev[]>(routes.revisions(pid)).then((r) => { setRevs(r); setError(null); }, (e) => setError(e.message));
  }, [pid, refreshTick.value]);
  async function approve(n: number) {
    if (pid === null) return;
    try { await post(routes.nodeApprove(pid), { target: "revision", n }); toast(`revision ${n} approved`); refresh(); } catch (e) { toastError(e); }
  }
  if (error) return <div class="callout danger">{error}</div>;
  if (!revs) return <p class="muted">loading…</p>;
  if (!revs.length) return <Empty text="No plan revisions yet." />;
  return (
    <div class="stack">
      {revs.map((rev) => (
        <div class="card stack tight">
          <div class="row between"><h3>Revision {rev.n}</h3><span class="caption">{rev.approved_at ? `approved ${rev.approved_at}` : "pending"}</span></div>
          <KeyValues value={rev.diff} />
          {!rev.approved_at && authed.value ? <div class="actions"><Button variant="filled" onClick={() => approve(rev.n)}>Approve revision</Button></div> : null}
        </div>
      ))}
    </div>
  );
}
