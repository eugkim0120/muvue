import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick, type NodeRow } from "../state";
import { route, closeNode } from "../router";
import { Sheet } from "../ui/Sheet";
import { Pill, Tier } from "../ui/Pill";
import { Actions } from "./Actions";
import { Overview } from "./Overview";
import { SpecBody } from "./SpecBody";
import { Flow } from "./Flow";
import { Runs } from "./Runs";
import { Discussion } from "./Discussion";
import { Details } from "./Details";

export type NodeDetail = {
  node: NodeRow & { body_md: string | null; criteria_json: string | null; criteria_mode: string; summary: string | null; block_reason: string | null; criteria_hash: string | null; deleted_at: string | null };
  notes: { kind: string; pinned: number | boolean; created_at: string; text: string }[];
  commits: { sha: string }[];
  predicted_touches: string[];
  verification: string;
};

export function NodeSheet() {
  const id = Number(route.value.query.get("node"));
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<NodeDetail>(routes.node(id)).then(
      (d) => { if (!alive) return; setDetail(d); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [id, refreshTick.value]);
  const title = detail ? `#${detail.node.id} ${detail.node.title}` : `#${id}`;
  return (
    <Sheet title={title} onClose={closeNode}>
      {error ? <div class="callout danger">{error}</div> : !detail ? <p class="muted">loading…</p> : (
        <div class="stack">
          <div class="row">
            <Pill status={detail.node.status} />
            <Tier tier={detail.node.risk_tier} />
            <span class="caption">{detail.node.kind}{detail.node.owner ? ` · owner ${detail.node.owner}` : ""}{detail.node.parent_id ? ` · parent #${detail.node.parent_id}` : ""}</span>
          </div>
          {detail.node.block_reason ? <div class="callout danger">blocked: {detail.node.block_reason}</div> : null}
          <Actions detail={detail} onDone={closeNode} />
          {detail.node.kind === "spec" ? <SpecBody nodeId={id} bodyMd={detail.node.body_md} notes={detail.notes} /> : <Overview detail={detail} />}
          <Flow receivesFrom={[]} sendsTo={[]} />
          <section><h3>Runs</h3><Runs nodeId={id} /></section>
          <section><h3>Discussion</h3><Discussion nodeId={id} notes={detail.notes} /></section>
          <Details nodeId={id} predictedTouches={detail.predicted_touches} />
        </div>
      )}
    </Sheet>
  );
}
