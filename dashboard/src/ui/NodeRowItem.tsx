import type { ComponentChildren } from "preact";
import type { NodeRow } from "../state";
import { openNode } from "../router";
import { Icon } from "./Icon";

export function NodeRowItem({ node, right, sub }: { node: NodeRow; right?: ComponentChildren; sub?: string }) {
  return (
    <button type="button" class="list-row" onClick={() => openNode(node.id)}>
      <span class="dot" style={{ background: `var(--st-${node.status})` }} />
      <span class="grow">
        <span class="title">#{node.id} {node.title}</span>
        {sub ? <span class="caption" style={{ display: "block" }}>{sub}</span> : null}
      </span>
      {right}
      <span class="chev"><Icon name="chevron" /></span>
    </button>
  );
}
