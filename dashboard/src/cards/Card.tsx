import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { toast, toastError } from "../state";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Discuss } from "./Discuss";
import type { Card as CardT } from "./cardsFromInbox";

const APPROVE_TARGET: Record<string, string> = { spec_review: "spec", gate2_review: "gate2", task_review: "review", awaiting_approval: "node" };
const CAPTION: Record<CardT["kind"], string> = { spec_review: "Spec awaiting approval", gate2_review: "Task list awaiting Gate 2", task_review: "Task in review", question: "Agent question", blocked: "Blocked", revision: "Plan revision proposed", ack: "Update", awaiting_approval: "Criteria changed, awaiting approval" };

export function Card({ card, onActed }: { card: CardT; onActed: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [discussing, setDiscussing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setError(null);
    try { await fn(); toast(label.toLowerCase()); onActed(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  }
  async function approve() {
    if (card.kind === "revision") return run("Approve", () => post(routes.approveRevision(card.projectId!), { target: "revision", n: Number(card.id.split(":")[1]) }));
    if (card.kind === "ack") return run("Acknowledge", () => post(routes.eventAck(Number(card.id.split(":").pop()))));
    const target = APPROVE_TARGET[card.kind];
    if (!target) return;
    // gate2 approval is keyed by the project id (core.gates.approve_gate2); every
    // other target here is keyed by the node's own id (core.gates.approve_spec,
    // core.nodes.approve_review, core.gates.approve_node).
    const id = card.kind === "gate2_review" ? card.projectId : card.nodeId;
    if (!id) return;
    return run("Approve", () => post(routes.nodeApprove(id), { target }));
  }
  async function reject() {
    if (!reason.trim() || !card.nodeId) return;
    return run("Send back", () => post(routes.nodeReject(card.nodeId!), { feedback: reason }));
  }

  return (
    <div class="card stack tight notif-card">
      <div class="row between">
        <span class="caption">{CAPTION[card.kind]}</span>
        <span class="caption">{card.ts}</span>
      </div>
      <button type="button" class="notif-title" disabled={!card.nodeId} onClick={() => card.nodeId && openNode(card.nodeId)}>{card.title}</button>
      <div>{card.context}</div>
      {card.agent ? <div class="caption">{card.agent}</div> : null}
      {error ? <div class="callout danger">{error}</div> : null}
      {card.kind === "ack" ? (
        !card.id.startsWith("ack:unverified:") ? (
          <div class="actions"><Button variant="filled" disabled={!!busy} onClick={approve}>{busy ? "…" : "Acknowledge"}</Button></div>
        ) : null
      ) : card.kind === "question" || card.kind === "blocked" ? (
        <div class="actions">
          <Button variant="plain" onClick={() => setDiscussing((d) => !d)}>{card.kind === "question" ? "Answer" : "Discuss"}</Button>
        </div>
      ) : (
        <div class="actions">
          <Button variant="filled" disabled={!!busy} onClick={approve}>{busy === "Approve" ? "…" : "Approve"}</Button>
          {card.kind === "task_review" ? <Button variant="danger" disabled={!!busy} onClick={() => setRejecting((r) => !r)}>Reject</Button> : null}
          {card.kind !== "revision" ? <Button variant="plain" onClick={() => setDiscussing((d) => !d)}>Discuss</Button> : null}
        </div>
      )}
      {rejecting ? (
        <div class="stack tight">
          <input placeholder="why?" value={reason} onInput={(e) => setReason((e.target as HTMLInputElement).value)} />
          <Button variant="danger" disabled={!reason.trim()} onClick={reject}>Send</Button>
        </div>
      ) : null}
      {discussing ? <Discuss card={card} onSent={onActed} /> : null}
    </div>
  );
}
