import type { NodeDetail } from "./NodeSheet";

const VERIFICATION: Record<string, string> = { checked_by_muvue: "checked by muvue", unverified: "unverified", human: "human" };

export function Overview({ detail }: { detail: NodeDetail }) {
  const n = detail.node;
  let criteria: unknown[] = [];
  try { criteria = JSON.parse(n.criteria_json || "[]"); } catch { criteria = [n.criteria_json]; }
  return (
    <div class="stack">
      {n.body_md ? <section><h3>Body</h3><pre>{n.body_md}</pre></section> : null}
      <section>
        <h3>Criteria <span class="caption">({n.criteria_mode}, {VERIFICATION[detail.verification] ?? detail.verification})</span></h3>
        {criteria.length ? <ul class="criteria">{criteria.map((c) => <li>{typeof c === "string" ? c : JSON.stringify(c)}</li>)}</ul> : <p class="muted">none</p>}
      </section>
      {n.summary ? <section><h3>Summary</h3><pre>{n.summary}</pre></section> : null}
      <section><h3>Predicted touches</h3><pre>{detail.predicted_touches.join("\n") || "none"}</pre></section>
      <section class="stack tight">
        <h3>Notes</h3>
        {detail.notes.length ? detail.notes.map((note) => (
          <div class="card">
            <div class="caption">{note.kind}{note.pinned ? " · pinned" : ""} · {note.created_at}</div>
            <div>{note.text}</div>
          </div>
        )) : <p class="muted">none</p>}
      </section>
      <section><h3>Commits</h3><pre class="mono">{detail.commits.map((c) => c.sha.slice(0, 12)).join("\n") || "none"}</pre></section>
    </div>
  );
}
