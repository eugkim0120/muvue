import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { toastError } from "../state";
import { Button } from "../ui/Button";
import type { Card } from "./cardsFromInbox";
import type { NodeDetail } from "../node/NodeSheet";

export function Discuss({ card, onSent }: { card: Card; onSent: () => void }) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [reply, setReply] = useState("");
  useEffect(() => { if (card.nodeId) api<NodeDetail>(routes.node(card.nodeId)).then(setDetail, toastError); }, [card.nodeId]);
  async function send() {
    if (!reply.trim()) return;
    try {
      if (card.kind === "question") await post(routes.questionAnswer(Number(card.id.split(":")[1])), { text: reply });
      else if (card.nodeId) await post(routes.nodeComment(card.nodeId), { text: reply });
      setReply(""); onSent();
    } catch (e) { toastError(e); }
  }
  return (
    <div class="stack tight discuss">
      {detail ? detail.notes.filter((n) => n.kind === "feedback").map((n) => <div class="callout">{n.text}</div>) : null}
      <div class="row" style={{ flexWrap: "nowrap" }}>
        <input placeholder="reply" value={reply} onInput={(e) => setReply((e.target as HTMLInputElement).value)} />
        <Button variant="filled" onClick={send}>Send</Button>
      </div>
    </div>
  );
}
