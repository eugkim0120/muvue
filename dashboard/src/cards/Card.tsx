import { useState } from "preact/hooks";
import { api, post } from "../api/client";
import { useApi } from "../hooks";
import { diffStat } from "../node/diff";
import { routes } from "../api/routes";
import { toast } from "../state";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Discuss } from "./Discuss";
import { useAction } from "../ui/useAction";
import type { Card as CardT } from "./cardsFromInbox";

const APPROVE_TARGET: Record<string, string> = { task_review: "review", awaiting_approval: "node" };
const CAPTION: Record<CardT["kind"], string> = { task_review: "Task in review", question: "Agent question", blocked: "Blocked", revision: "Plan revision proposed", ack: "Update", awaiting_approval: "Criteria changed, awaiting approval" };

function ReviewFacts({ card }: { card: CardT }) {
  const diffQ = useApi<{ diff: string; source: string }>(() => api(routes.nodeDiff(card.nodeId!)), [card.nodeId]);
  const stat = diffQ.data && diffQ.data.source !== "none" ? diffStat(diffQ.data.diff) : null;
  return (
    <div class="review-facts caption">
      {diffQ.error ? <span>diff unavailable</span> : diffQ.data ? (stat ? <span class="mono">{`+${stat.added} \u2212${stat.removed}`}</span> : <span>no changes recorded</span>) : <span>…</span>}
      {card.tier ? <span> · {card.tier} risk</span> : null}
      {card.ownerLabel ? <span aria-label="owner"> · {card.ownerLabel}</span> : null}
      <button type="button" class="link-btn" onClick={() => card.nodeId && openNode(card.nodeId)}>Open task</button>
    </div>
  );
}

export function Card({ card, onActed, readOnly = false }: { card: CardT; onActed: () => void; readOnly?: boolean }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [discussing, setDiscussing] = useState(false);
  const approveA = useAction();
  const rejectA = useAction();

  const approveLabel = card.kind === "ack" ? "Acknowledge" : "Approve";
  async function approve() {
    let fn: (() => Promise<unknown>) | null = null;
    if (card.kind === "revision") fn = () => post(routes.approveRevision(card.projectId!), { target: "revision", n: Number(card.id.split(":")[1]) });
    else if (card.kind === "ack") fn = () => post(routes.eventAck(Number(card.id.split(":").pop())));
    else {
      const target = APPROVE_TARGET[card.kind];
      const id = card.nodeId;
      if (target && id) fn = () => post(routes.nodeApprove(id), { target });
    }
    if (!fn) return;
    const ok = await approveA.run(fn);
    if (ok) { toast(approveLabel.toLowerCase()); onActed(); }
  }
  async function reject() {
    if (!reason.trim() || !card.nodeId) return;
    const ok = await rejectA.run(() => post(routes.nodeReject(card.nodeId!), { feedback: reason }));
    if (ok) { toast("send back"); onActed(); }
  }

  return (
    <div class="card stack tight notif-card">
      <div class="row between">
        <span class="caption">{CAPTION[card.kind]}</span>
        <span class="caption">{card.ts}</span>
      </div>
      <button type="button" class="notif-title" disabled={!card.nodeId} onClick={() => card.nodeId && openNode(card.nodeId)}>{card.title}</button>
      <div class="clamp-3">{card.context}</div>
      {card.kind === "task_review" && card.nodeId ? <ReviewFacts card={card} /> : null}
      {card.agent ? <div class="caption">{card.agent}</div> : null}
      {approveA.error ? <div class="callout danger">{approveA.error}</div> : null}
      {readOnly ? null : card.kind === "ack" ? (
        !card.id.startsWith("ack:unverified:") ? (
          <div class="actions"><Button variant="filled" busy={approveA.busy} busyLabel="Acknowledging…" onClick={approve}>Acknowledge</Button></div>
        ) : null
      ) : card.kind === "question" || card.kind === "blocked" ? (
        <div class="actions">
          <Button variant="plain" onClick={() => setDiscussing((d) => !d)}>{card.kind === "question" ? "Answer" : "Discuss"}</Button>
        </div>
      ) : (
        <div class="actions">
          <Button variant="filled" busy={approveA.busy} busyLabel="Approving…" onClick={approve}>Approve</Button>
          {card.kind === "task_review" ? <Button variant="danger" disabled={approveA.busy} onClick={() => setRejecting((r) => !r)}>Reject</Button> : null}
          {card.kind !== "revision" ? <Button variant="plain" onClick={() => setDiscussing((d) => !d)}>Discuss</Button> : null}
        </div>
      )}
      {!readOnly && rejecting ? (
        <div class="stack tight">
          <input placeholder="why?" value={reason} onInput={(e) => setReason((e.target as HTMLInputElement).value)} />
          {rejectA.error ? <div class="callout danger">{rejectA.error}</div> : null}
          <Button variant="danger" disabled={!reason.trim()} busy={rejectA.busy} busyLabel="Sending…" onClick={reject}>Send</Button>
        </div>
      ) : null}
      {discussing ? <Discuss card={card} onSent={onActed} /> : null}
    </div>
  );
}
