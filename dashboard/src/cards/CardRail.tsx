import { useEffect, useRef, useState } from "preact/hooks";
import type { Card as CardT } from "./cardsFromInbox";
import { Card } from "./Card";
import { refresh } from "../state";

const RAIL_ID = "needs-you-rail";

export function CardRail({ cards, authed }: { cards: CardT[]; authed: boolean }) {
  // Collapsed once on first render for phones; a later resize does not re-collapse, by design.
  const [collapsed, setCollapsed] = useState(() => typeof matchMedia === "function" && matchMedia("(max-width: 899px)").matches);
  const chipRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLSpanElement>(null);
  const toggled = useRef(false);
  // The clicked button unmounts on every toggle, so hand focus to its counterpart.
  useEffect(() => {
    if (!toggled.current) return;
    (collapsed ? chipRef.current : headingRef.current)?.focus();
  }, [collapsed]);
  function toggle(next: boolean) { toggled.current = true; setCollapsed(next); }
  if (!cards.length) return null;
  const n = cards.length;
  if (collapsed) return <button ref={chipRef} type="button" class="card-rail-pill" aria-expanded="false" aria-controls={RAIL_ID} onClick={() => toggle(false)}>{n === 1 ? "1 thing needs you" : `${n} things need you`}</button>;
  return (
    <div id={RAIL_ID} class="card-rail stack tight">
      <div class="row between"><span ref={headingRef} tabIndex={-1} class="caption">Needs you · {cards.length}</span><button type="button" class="btn btn-plain" aria-label="hide needs-you list" aria-expanded="true" aria-controls={RAIL_ID} onClick={() => toggle(true)}>Hide</button></div>
      {cards.map((c) => <Card key={c.id} card={c} readOnly={!authed} onActed={refresh} />)}
    </div>
  );
}
