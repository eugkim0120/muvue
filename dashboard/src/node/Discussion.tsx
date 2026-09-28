import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toastError } from "../state";
import { Button } from "../ui/Button";

type Note = { kind: string; text: string; created_at: string };

export function Discussion({ nodeId, notes }: { nodeId: number; notes: Note[] }) {
  const [reply, setReply] = useState("");
  async function send() {
    if (!reply.trim()) return;
    try { await post(routes.nodeComment(nodeId), { text: reply }); setReply(""); refresh(); } catch (e) { toastError(e); }
  }
  return (
    <div class="stack tight">
      {notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>)}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" onClick={send}>Send</Button>
      </div>
    </div>
  );
}
