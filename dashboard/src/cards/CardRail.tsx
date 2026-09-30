import { useState } from "preact/hooks";
import type { Card as CardT } from "./cardsFromInbox";
import { Card } from "./Card";
import { refresh, signInOpen } from "../state";
import { Button } from "../ui/Button";

export function CardRail({ cards, authed }: { cards: CardT[]; authed: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!cards.length) return null;
  if (collapsed) return <button type="button" class="card-rail-pill" onClick={() => setCollapsed(false)}>{cards.length} need you</button>;
  return (
    <div class="card-rail stack tight">
      <div class="row between"><span class="caption">Needs you · {cards.length}</span><button type="button" class="btn btn-plain" aria-label="hide needs-you list" onClick={() => setCollapsed(true)}>Hide</button></div>
      {authed ? null : <div data-sign-in-to-act><Button variant="plain" onClick={() => { signInOpen.value = true; }}>Sign in to act</Button></div>}
      {cards.map((c) => <Card key={c.id} card={c} readOnly={!authed} onActed={refresh} />)}
    </div>
  );
}
