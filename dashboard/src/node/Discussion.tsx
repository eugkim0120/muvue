import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh } from "../state";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";

type Note = { kind: string; text: string; created_at: string };

export function Discussion({ nodeId, notes }: { nodeId: number; notes: Note[] }) {
  const [reply, setReply] = useState("");
  const a = useAction();
  async function send() {
    if (!reply.trim()) return;
    const ok = await a.run(() => post(routes.nodeComment(nodeId), { text: reply }));
    if (ok) { setReply(""); refresh(); }
  }
  return (
    <div class="stack tight">
      {notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>)}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" busy={a.busy} busyLabel="Sending…" onClick={send}>Send</Button>
      </div>
      {a.error ? <div class="callout danger">{a.error}</div> : null}
    </div>
  );
}
