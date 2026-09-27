import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast, toastError, type NodeRow, type Project } from "../state";
import { openNode, navigate } from "../router";
import { Sheet } from "../ui/Sheet";

export type Item = { label: string; hint?: string; run: () => void };
type Action = { label: string; run: () => void };

export function paletteItems(query: string, nodes: NodeRow[], projs: Project[], actions: Action[]): Item[] {
  const q = query.trim().toLowerCase();
  const hit = (s: string) => !q || s.toLowerCase().includes(q);
  const items: Item[] = [];
  for (const a of actions) if (hit(a.label)) items.push({ label: a.label, hint: "action", run: a.run });
  for (const n of nodes) {
    const label = `#${n.id} ${n.title}`;
    if (!q || String(n.id) === q || hit(n.title)) items.push({ label, hint: n.status.replace(/_/g, " "), run: () => openNode(n.id) });
  }
  for (const p of projs) {
    const label = `Switch to #${p.id} ${p.goal}`;
    if (hit(label)) items.push({ label, hint: p.phase, run: () => { projectId.value = p.id; refresh(); navigate("#/plan"); } });
  }
  return items;
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [cursor, setCursor] = useState(0);
  useEffect(() => { api<NodeRow[]>(routes.nodes(projectId.value)).then(setNodes, toastError); }, []);
  const p = currentProject.value;
  const actions: Action[] = [];
  if (authed.value && p) {
    const act = (label: string, path: string, body?: unknown) => actions.push({ label, run: () => { post(path, body).then(() => { toast(label.toLowerCase() + ": done"); refresh(); }, toastError); onClose(); } });
    if (p.phase !== "paused" && p.phase !== "closed") act("Pause", routes.projectPause(p.id));
    if (p.phase === "paused") act("Resume", routes.projectResume(p.id));
    const spec = nodes.find((n) => n.kind === "spec" && n.status === "pending");
    if (spec) act("Approve spec", routes.nodeApprove(spec.id), { target: "spec" });
    if (p.phase === "planning" && !spec && nodes.some((n) => n.kind !== "spec")) act("Approve task list", routes.nodeApprove(p.id), { target: "gate2" });
  }
  const items = paletteItems(query, nodes, projects.value, actions).slice(0, 20);
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(items.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const it = items[cursor]; if (it) { it.run(); onClose(); } }
  }
  return (
    <Sheet title="Jump to" onClose={onClose}>
      <div class="stack tight">
        <input autofocus placeholder="task number, title, project or action" value={query} onInput={(e) => { setQuery((e.target as HTMLInputElement).value); setCursor(0); }} onKeyDown={onKey} />
        <div class="list">
          {items.map((it, i) => (
            <button type="button" class={"list-row" + (i === cursor ? " on" : "")} onClick={() => { it.run(); onClose(); }}>
              <span class="grow title">{it.label}</span>{it.hint ? <span class="caption">{it.hint}</span> : null}
            </button>
          ))}
          {!items.length ? <div class="list-row muted">no matches</div> : null}
        </div>
      </div>
    </Sheet>
  );
}
