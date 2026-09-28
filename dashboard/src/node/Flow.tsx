import { openNode } from "../router";

type Link = { id: number; title: string; carries: string | null };

export function Flow({ receivesFrom, sendsTo }: { receivesFrom: Link[]; sendsTo: Link[] }) {
  if (!receivesFrom.length && !sendsTo.length) return null;
  return (
    <div class="stack tight">
      {receivesFrom.map((l) => <button type="button" class="list-row" onClick={() => openNode(l.id)}>receives {l.carries ? l.carries + " " : ""}from #{l.id} {l.title}</button>)}
      {sendsTo.map((l) => <button type="button" class="list-row" onClick={() => openNode(l.id)}>sends {l.carries ? l.carries + " " : ""}to #{l.id} {l.title}</button>)}
    </div>
  );
}
