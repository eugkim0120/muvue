import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast, toastError, type NodeRow, type Project } from "../state";
import { openNode, navigate } from "../router";
import { Sheet } from "../ui/Sheet";
import { Confirm } from "../ui/Confirm";
import { CloseSheet } from "../project/CloseSheet";

export type Item = { label: string; hint?: string; run: () => void; keepOpen?: boolean };
type Action = { label: string; run: () => void; keepOpen?: boolean };

export function paletteItems(query: string, nodes: NodeRow[], projs: Project[], actions: Action[]): Item[] {
  const q = query.trim().toLowerCase();
  const hit = (s: string) => !q || s.toLowerCase().includes(q);
  const items: Item[] = [];
  for (const a of actions) if (hit(a.label)) items.push({ label: a.label, hint: "action", run: a.run, keepOpen: a.keepOpen });
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
  const [confirmPause, setConfirmPause] = useState(false);
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    if (projectId.value == null) { setNodes([]); return; }
    let alive = true;
    api<NodeRow[]>(routes.nodes(projectId.value)).then((n) => { if (alive) setNodes(n); }, (e) => { if (alive) toastError(e); });
    return () => { alive = false; };
  }, []);
  const p = currentProject.value;

  async function pause() {
    if (!p) return;
    try {
      const r = await post<{ result?: { stopped_runners?: unknown[] }; stopped_runners?: unknown[] }>(routes.projectPause(p.id));
      const stopped = (r.result ?? r).stopped_runners ?? [];
      toast(`paused; stopped ${stopped.length} runner process(es)`);
      refresh();
      onClose();
    } catch (e) { toastError(e); }
  }

  if (closing && p) return <CloseSheet projectId={p.id} onClose={() => { setClosing(false); onClose(); }} />;
  if (confirmPause) {
    return <Confirm title="Pause project?" body="Running agents are stopped and their tasks go back to ready." confirmLabel="Pause" danger onConfirm={pause} onCancel={() => setConfirmPause(false)} />;
  }

  const actions: Action[] = [];
  if (authed.value && p) {
    const act = (label: string, path: string, body?: unknown) => actions.push({ label, run: () => { post(path, body).then(() => { toast(label.toLowerCase() + ": done"); refresh(); }, toastError); onClose(); } });
    if (p.phase !== "paused" && p.phase !== "closed") actions.push({ label: "Pause", run: () => setConfirmPause(true), keepOpen: true });
    if (p.phase === "paused") act("Resume", routes.projectResume(p.id));
    if (p.phase !== "closed") actions.push({ label: "Close project…", run: () => setClosing(true), keepOpen: true });
    const spec = nodes.find((n) => n.kind === "spec" && n.status === "pending");
    if (spec) act("Approve spec", routes.nodeApprove(spec.id), { target: "spec" });
    if (p.phase === "planning" && !spec && nodes.some((n) => n.kind !== "spec")) act("Approve task list", routes.nodeApprove(p.id), { target: "gate2" });
  }
  const items = paletteItems(query, nodes, projects.value, actions).slice(0, 20);
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(items.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const it = items[cursor]; if (it) { it.run(); if (!it.keepOpen) onClose(); } }
  }
  return (
    <Sheet title="Jump to" onClose={onClose}>
      <div class="stack tight">
        <input autofocus placeholder="task number, title, project or action" value={query} onInput={(e) => { setQuery((e.target as HTMLInputElement).value); setCursor(0); }} onKeyDown={onKey} />
        <div class="list">
          {items.map((it, i) => (
            <button type="button" class={"list-row" + (i === cursor ? " on" : "")} onClick={() => { it.run(); if (!it.keepOpen) onClose(); }}>
              <span class="grow title">{it.label}</span>{it.hint ? <span class="caption">{it.hint}</span> : null}
            </button>
          ))}
          {!items.length ? <div class="list-row muted">no matches</div> : null}
        </div>
      </div>
    </Sheet>
  );
}
