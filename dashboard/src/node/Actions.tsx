import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { StartPicker } from "./StartPicker";
import type { NodeDetail } from "./NodeSheet";

export function Actions({ detail, onDone }: { detail: NodeDetail; onDone: () => void }) {
  const n = detail.node;
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [starting, setStarting] = useState(false);
  if (!authed.value) return null;

  async function approve(target: string, label: string) {
    try { await post(routes.nodeApprove(n.id), { target }); toast(label); refresh(); onDone(); } catch (e) { toastError(e); }
  }
  async function reject() {
    if (!feedback.trim()) return;
    try { await post(routes.nodeReject(n.id), { feedback }); toast("sent back with feedback"); refresh(); onDone(); } catch (e) { toastError(e); }
  }

  const buttons = [];
  if (n.status === "review") {
    buttons.push(<Button variant="filled" onClick={() => approve("review", "approved")}>Approve</Button>);
    buttons.push(<Button variant="danger" onClick={() => setRejecting(true)}>Reject</Button>);
  }
  if (n.status === "awaiting_approval") buttons.push(<Button variant="filled" onClick={() => approve("node", "criteria approved")}>Approve changed criteria</Button>);
  if (n.kind === "spec" && n.status === "pending") buttons.push(<Button variant="filled" onClick={() => approve("spec", "spec approved")}>Approve spec</Button>);
  if (n.status === "ready") buttons.push(<Button variant="filled" onClick={() => setStarting(true)}>Start with agent</Button>);
  if (!buttons.length) return null;

  return (
    <div class="stack tight">
      <div class="actions">{buttons}</div>
      {rejecting ? (
        <div class="stack tight">
          <textarea placeholder="what should change?" value={feedback} onInput={(e) => setFeedback((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button variant="danger" onClick={reject}>Send</Button><Button variant="plain" onClick={() => setRejecting(false)}>Cancel</Button></div>
        </div>
      ) : null}
      {starting ? <StartPicker nodeId={n.id} onClose={() => setStarting(false)} onStarted={onDone} /> : null}
    </div>
  );
}
