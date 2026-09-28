import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { toastError } from "../state";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import type { Card } from "./cardsFromInbox";
import type { NodeDetail } from "../node/NodeSheet";

export function Discuss({ card, onSent }: { card: Card; onSent: () => void }) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [reply, setReply] = useState("");
  const a = useAction();
  useEffect(() => {
    let alive = true;
    if (card.nodeId) api<NodeDetail>(routes.node(card.nodeId)).then(
      (d) => { if (alive) setDetail(d); },
      (e) => { if (alive) toastError(e); },
    );
    return () => { alive = false; };
  }, [card.nodeId]);
  async function send() {
    if (!reply.trim()) return;
    const ok = await a.run(async () => {
      if (card.kind === "question") await post(routes.questionAnswer(Number(card.id.split(":")[1])), { text: reply });
      else if (card.nodeId) await post(routes.nodeComment(card.nodeId), { text: reply });
    });
    if (ok) { setReply(""); onSent(); }
  }
  return (
    <div class="stack tight discuss">
      {detail ? detail.notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>) : null}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" busy={a.busy} busyLabel="Sending…" onClick={send}>Send</Button>
      </div>
      {a.error ? <div class="callout danger">{a.error}</div> : null}
    </div>
  );
}
