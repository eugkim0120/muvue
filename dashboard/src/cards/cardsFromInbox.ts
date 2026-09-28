import type { NodeRow } from "../state";
import type { CanvasSpec } from "../canvas/canvasData";

type Ev = { id: number; ts: string; node_id: number | null; project_id: number | null; type: string; payload: string };
type Question = { id: number; node_id: number; text: string; default_answer: string | null; default_ok: boolean };
export type Inbox = {
  questions: Question[]; review: NodeRow[]; unverified_external: { node_id: number; title: string }[];
  structure_updates: Ev[]; blocked: NodeRow[]; awaiting_approval: NodeRow[]; signals: Ev[]; audit_items: Ev[]; unattributed_commits: Ev[];
};
export type Revision = { n: number; approved_at: string | null };
export type NodesById = Record<number, { title: string; project_id: number }>;

export type CardKind = "spec_review" | "gate2_review" | "task_review" | "question" | "blocked" | "revision" | "ack";
export type Card = { id: string; kind: CardKind; nodeId: number | null; projectId: number | null; title: string; context: string; agent: string; ts: string; raw: unknown };

export type Ctx = { spec: CanvasSpec | null; taskCount: number; projectPhase: string; revisions: Revision[]; nodesById: NodesById; projectId: number };

function payloadOf(ev: Ev): Record<string, unknown> { try { return JSON.parse(ev.payload || "{}"); } catch { return {}; } }

export function cardsFromInbox(inbox: Inbox, ctx: Ctx): Card[] {
  const cards: Card[] = [];
  if (ctx.spec && ctx.spec.status === "pending") {
    cards.push({ id: "spec_review:" + ctx.spec.id, kind: "spec_review", nodeId: ctx.spec.id, projectId: ctx.nodesById[ctx.spec.id]?.project_id ?? null, title: ctx.spec.title, context: "spec awaiting approval", agent: "", ts: "" });
  }
  if (ctx.spec && ctx.spec.status !== "pending" && ctx.taskCount > 0 && ctx.projectPhase === "planning") {
    cards.push({ id: "gate2_review:" + ctx.spec.id, kind: "gate2_review", nodeId: ctx.spec.id, projectId: ctx.nodesById[ctx.spec.id]?.project_id ?? null, title: ctx.spec.title, context: `${ctx.taskCount} tasks awaiting Gate 2`, agent: "", ts: "" });
  }
  for (const q of inbox.questions) {
    const n = ctx.nodesById[q.node_id];
    cards.push({ id: "question:" + q.id, kind: "question", nodeId: q.node_id, projectId: n?.project_id ?? null, title: n?.title ?? `#${q.node_id}`, context: q.text, agent: "", ts: "" });
  }
  for (const n of inbox.review) cards.push({ id: "task_review:" + n.id, kind: "task_review", nodeId: n.id, projectId: n.project_id, title: n.title, context: "in review", agent: n.owner ?? "", ts: "" });
  for (const n of inbox.blocked) cards.push({ id: "blocked:" + n.id, kind: "blocked", nodeId: n.id, projectId: n.project_id, title: n.title, context: n.block_reason ?? "blocked", agent: n.owner ?? "", ts: "" });
  for (const r of ctx.revisions.filter((r) => r.approved_at === null)) {
    cards.push({ id: "revision:" + r.n, kind: "revision", nodeId: ctx.projectId, projectId: ctx.projectId, title: `Revision ${r.n}`, context: "plan revision proposed", agent: "", ts: "" });
  }
  const ackSources: Ev[] = [...inbox.structure_updates, ...inbox.audit_items, ...inbox.signals, ...inbox.unattributed_commits];
  for (const ev of ackSources) {
    const p = payloadOf(ev);
    cards.push({ id: "ack:" + ev.id, kind: "ack", nodeId: ev.node_id, projectId: ev.project_id, title: ev.type, context: String(p.message ?? ev.type), agent: "", ts: ev.ts, raw: ev });
  }
  for (const u of inbox.unverified_external) cards.push({ id: "ack:unverified:" + u.node_id, kind: "ack", nodeId: u.node_id, projectId: ctx.nodesById[u.node_id]?.project_id ?? null, title: u.title, context: "unverified external criteria", agent: "", ts: "" });
  return cards;
}
