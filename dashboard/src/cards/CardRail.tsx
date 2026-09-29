import { useState } from "preact/hooks";
import type { Card as CardT } from "./cardsFromInbox";
import { Card } from "./Card";
import { refresh } from "../state";

export function CardRail({ cards }: { cards: CardT[] }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!cards.length) return null;
  if (collapsed) return <button type="button" class="card-rail-pill" onClick={() => setCollapsed(false)}>{cards.length} need you</button>;
  return (
    <div class="card-rail stack tight">
      <div class="row between"><span class="caption">Needs you · {cards.length}</span><button type="button" class="icon-btn" aria-label="collapse" onClick={() => setCollapsed(true)}>—</button></div>
      {cards.map((c) => <Card key={c.id} card={c} onActed={refresh} />)}
    </div>
  );
}
