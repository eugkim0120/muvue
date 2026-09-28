import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast } from "../state";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { StartPicker } from "./StartPicker";
import { startBreakdown } from "../canvas/pending";
import type { NodeDetail } from "./NodeSheet";
import { useAction } from "../ui/useAction";

export function Actions({ detail, onDone }: { detail: NodeDetail; onDone: () => void }) {
  const n = detail.node;
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [starting, setStarting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const approveA = useAction();
  const rejectA = useAction();
  const removeA = useAction();
  const breakdownA = useAction();
  if (!authed.value) return null;

  async function approve(target: string, label: string) {
    const ok = await approveA.run(() => post(routes.nodeApprove(n.id), { target }));
    if (ok) { toast(label); refresh(); onDone(); }
  }
  async function reject() {
    if (!feedback.trim()) return;
    const ok = await rejectA.run(() => post(routes.nodeReject(n.id), { feedback }));
    if (ok) { toast("sent back with feedback"); refresh(); onDone(); }
  }
  async function remove() {
    const ok = await removeA.run(() => post(routes.nodeRemove(n.id)));
    if (ok) { setRemoving(false); toast("removed"); refresh(); onDone(); }
  }
  async function breakDown() {
    const ok = await breakdownA.run(async () => {
      const r = await post<{ spawned: { agent: string; log: string } }>(routes.nodeBreakdown(n.id), {});
      startBreakdown(n.id, { nodeId: n.id, agent: r.spawned.agent, log: r.spawned.log, startChildIds: new Set() });
    });
    if (ok) { toast("breakdown started"); refresh(); }
  }

  const buttons = [];
  if (n.status === "review") {
    buttons.push(<Button variant="filled" busy={approveA.busy} busyLabel="Approving…" onClick={() => approve("review", "approved")}>Approve</Button>);
    buttons.push(<Button variant="danger" onClick={() => setRejecting(true)}>Reject</Button>);
  }
  if (n.status === "awaiting_approval") buttons.push(<Button variant="filled" busy={approveA.busy} busyLabel="Approving…" onClick={() => approve("node", "criteria approved")}>Approve changed criteria</Button>);
  if (n.kind === "spec" && n.status === "pending") buttons.push(<Button variant="filled" busy={approveA.busy} busyLabel="Approving…" onClick={() => approve("spec", "spec approved")}>Approve spec</Button>);
  if (n.status === "ready") buttons.push(<Button variant="filled" onClick={() => setStarting(true)}>Start with agent</Button>);
  if ((n.kind === "spec" || n.kind === "task") && n.deleted_at === null) buttons.push(<Button variant="outline" busy={breakdownA.busy} busyLabel="Starting…" onClick={breakDown}>✨ Break down with agent</Button>);
  if (n.criteria_hash === null && n.deleted_at === null && n.kind !== "spec") buttons.push(<Button variant="danger" onClick={() => setRemoving(true)}>Remove</Button>);
  if (!buttons.length) return null;

  return (
    <div class="stack tight">
      <div class="actions">{buttons}</div>
      {approveA.error ? <div class="callout danger">{approveA.error}</div> : null}
      {breakdownA.error ? <div class="callout danger">{breakdownA.error}</div> : null}
      {rejecting ? (
        <div class="stack tight">
          <textarea placeholder="what should change?" value={feedback} onInput={(e) => setFeedback((e.target as HTMLTextAreaElement).value)} />
          {rejectA.error ? <div class="callout danger">{rejectA.error}</div> : null}
          <div class="actions"><Button variant="danger" busy={rejectA.busy} busyLabel="Sending…" onClick={reject}>Send</Button><Button variant="plain" onClick={() => setRejecting(false)}>Cancel</Button></div>
        </div>
      ) : null}
      {starting ? <StartPicker nodeId={n.id} onClose={() => setStarting(false)} onStarted={onDone} /> : null}
      {removing ? (
        <Confirm
          title="Remove this task?"
          body={<>It and its subtasks are soft-deleted; this cannot be undone from the page.{removeA.error ? <div class="callout danger">{removeA.error}</div> : null}</>}
          confirmLabel={removeA.busy ? "Removing…" : "Remove"}
          danger
          onConfirm={remove}
          onCancel={() => setRemoving(false)}
        />
      ) : null}
    </div>
  );
}
