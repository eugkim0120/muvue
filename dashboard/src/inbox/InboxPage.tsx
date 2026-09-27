import { useEffect, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, inboxCount, refreshTick, refresh, toast, toastError, type NodeRow } from "../state";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";
import { NodeRowItem } from "../ui/NodeRowItem";
import { Tier } from "../ui/Pill";
import { classifyDiffLine } from "../node/diff";

type Ev = { id: number; ts: string; node_id: number | null; payload: string };
type Question = { id: number; node_id: number; text: string; default_answer: string | null; default_ok: boolean };
export type Inbox = {
  questions: Question[]; review: NodeRow[]; unverified_external: { node_id: number; title: string }[];
  awaiting_approval: NodeRow[]; blocked: NodeRow[]; structure_updates: Ev[]; audit_items: Ev[]; signals: Ev[]; unattributed_commits: Ev[];
};

export function countInbox(d: Inbox): number {
  return d.questions.length + d.review.length + d.unverified_external.length + d.awaiting_approval.length + d.blocked.length + d.structure_updates.length + d.audit_items.length + d.signals.length + d.unattributed_commits.length;
}

function payloadOf(ev: Ev): Record<string, unknown> {
  try { return JSON.parse(ev.payload || "{}"); } catch { return {}; }
}

function Section({ title, count, children }: { title: string; count: number; children: ComponentChildren }) {
  if (!count) return null;
  return <section class="stack tight"><h2>{title} <span class="caption">{count}</span></h2>{children}</section>;
}

function AckCard({ ev, summary, children }: { ev: Ev; summary: string; children?: ComponentChildren }) {
  async function ack() { try { await post(routes.eventAck(ev.id)); toast("acknowledged"); refresh(); } catch (e) { toastError(e); } }
  return (
    <div class="card stack tight">
      <div class="row between"><div>{summary}</div>{authed.value ? <Button onClick={ack}>Ack</Button> : null}</div>
      <div class="caption">{ev.ts}{ev.node_id ? ` · task #${ev.node_id}` : ""}</div>
      {children}
    </div>
  );
}

function QuestionCard({ q }: { q: Question }) {
  const [answer, setAnswer] = useState("");
  async function send() {
    if (!answer.trim()) return;
    try { await post(routes.questionAnswer(q.id), { text: answer }); toast("answered"); refresh(); } catch (e) { toastError(e); }
  }
  return (
    <form class="card stack tight" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <div>{q.text}</div>
      <div class="caption">task #{q.node_id} · default: {q.default_answer || "none"}{q.default_ok ? " (safe to default)" : ""}</div>
      {authed.value ? <div class="row" style={{ flexWrap: "nowrap" }}><input placeholder="answer" value={answer} onInput={(e) => setAnswer((e.target as HTMLInputElement).value)} /><Button type="submit" variant="filled">Answer</Button></div> : null}
    </form>
  );
}

export function InboxPage() {
  const [data, setData] = useState<Inbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Inbox>(routes.inbox()).then((d) => { setData(d); setError(null); inboxCount.value = countInbox(d); }, (e) => setError(e.message)); }, [refreshTick.value]);
  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!data) return <div class="page"><p class="muted">loading…</p></div>;
  const rowOf = (n: { node_id: number; title: string }): NodeRow => ({ id: n.node_id, title: n.title, project_id: 0, parent_id: null, kind: "task", status: "review", risk_tier: "low", owner: null });
  return (
    <div class="page stack">
      <h1 class="page-title">Inbox</h1>
      {countInbox(data) === 0 ? <Empty text="Nothing waiting on you." check /> : null}
      <Section title="Questions" count={data.questions.length}>{data.questions.map((q) => <QuestionCard q={q} />)}</Section>
      <Section title="Awaiting review" count={data.review.length}><div class="list">{data.review.map((n) => <NodeRowItem node={n} right={<Tier tier={n.risk_tier} />} />)}</div></Section>
      <Section title="Unverified external criteria" count={data.unverified_external.length}>
        <div class="list">{data.unverified_external.map((u) => <NodeRowItem node={rowOf(u)} sub="muvue could not run these checks; verify before approving" />)}</div>
      </Section>
      <Section title="Criteria changed, awaiting approval" count={data.awaiting_approval.length}><div class="list">{data.awaiting_approval.map((n) => <NodeRowItem node={n} />)}</div></Section>
      <Section title="Blocked" count={data.blocked.length}><div class="list">{data.blocked.map((n) => <NodeRowItem node={n} sub={n.block_reason || undefined} />)}</div></Section>
      <Section title="Structure updates" count={data.structure_updates.length}>
        {data.structure_updates.map((ev) => { const p = payloadOf(ev); return (
          <AckCard ev={ev} summary={`structure diff on ${p.ref} (${String(p.sha || "").slice(0, 12)})`}>
            {p.message ? <div class="muted">{String(p.message)}</div> : null}
            {(() => {
              if (!p.pr_url) return null;
              const url = typeof p.pr_url === "string" && /^https?:\/\//i.test(p.pr_url) ? p.pr_url : null;
              return url ? <a href={url} target="_blank" rel="noopener">{url}</a> : <span class="muted">{String(p.pr_url)}</span>;
            })()}
            {p.pr_error ? <div class="muted">PR not opened: {String(p.pr_error)}</div> : null}
          </AckCard>); })}
      </Section>
      <Section title="Audit drafts" count={data.audit_items.length}>
        {data.audit_items.map((ev) => { const p = payloadOf(ev); return (
          <AckCard ev={ev} summary={`component #${p.component_id} ${p.name || ""}: ${p.message || "re-verify"}`}>
            <details><summary class="caption">diff</summary><pre class="scroll-x">{String(p.diff || "").split("\n").map((l) => <span class={classifyDiffLine(l)}>{l + "\n"}</span>)}</pre></details>
          </AckCard>); })}
      </Section>
      <Section title="Commits touching components without a task" count={data.signals.length}>
        {data.signals.map((ev) => { const p = payloadOf(ev); return <AckCard ev={ev} summary={`${String(p.sha || "").slice(0, 12)} touched ${((p.files as string[]) || []).join(", ")}`} />; })}
      </Section>
      <Section title="Unattributed commits" count={data.unattributed_commits.length}>
        {data.unattributed_commits.map((ev) => { const p = payloadOf(ev); return <AckCard ev={ev} summary={`${String(p.sha || "").slice(0, 12)} has no resolvable Muvue-Node trailer`}><div class="muted">{((p.files as string[]) || []).join(", ")}</div></AckCard>; })}
      </Section>
    </div>
  );
}
