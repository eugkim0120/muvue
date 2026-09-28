import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import type { CanvasSpec } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";
import { AgentChip } from "./AgentChip";
import { agentStateOf } from "./agentState";
import { BreakdownButton } from "./AddForm";

function SubmitSpecForm({ projectId }: { projectId: number }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: Event) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    try { await post(routes.projectSpec(projectId), { title, body_md: body }); toast("spec submitted"); refresh(); } catch (e) { toastError(e); } finally { setBusy(false); }
  }
  return (
    <form class="spec-root-card stack" onSubmit={submit}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one requirement per line" value={body} onInput={(e) => setBody((e.target as HTMLTextAreaElement).value)} />
      <div class="actions"><Button type="submit" variant="filled" disabled={busy}>Submit spec</Button></div>
    </form>
  );
}

export function SpecRoot({ spec, projectId, taskCount, projectPhase }: { spec: CanvasSpec | null; projectId: number; taskCount: number; projectPhase: string }) {
  if (!spec) return authed.value ? <SubmitSpecForm projectId={projectId} /> : <div class="spec-root-card"><p class="muted">No spec yet.</p></div>;
  const canApproveSpec = authed.value && spec.status === "pending";
  const canApproveTasks = authed.value && spec.status !== "pending" && taskCount > 0 && projectPhase === "planning";
  const state = agentStateOf({ status: spec.status, owner: null, agent: spec.agent }, projectPhase, false);

  async function approveSpec() { try { await post(routes.nodeApprove(spec!.id), { target: "spec" }); toast("spec approved"); refresh(); } catch (e) { toastError(e); } }
  async function approveTasks() { try { await post(routes.nodeApprove(projectId), { target: "gate2" }); toast("task list approved; criteria frozen"); refresh(); } catch (e) { toastError(e); } }

  return (
    <div class="spec-root-card stack" tabIndex={0} onClick={() => openNode(spec.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(spec.id); }}>
      <div class="row between">
        <span class="title grow">{spec.title}</span>
        <Pill status={spec.status} />
      </div>
      {spec.body_md ? <div class="caption">{purposeLine(spec.body_md)}</div> : null}
      <div class="row between">
        <AgentChip agent={spec.agent} state={state} />
        <div class="actions">
          {canApproveSpec ? <Button variant="filled" onClick={(e: Event) => { e.stopPropagation(); void approveSpec(); }}>Approve spec</Button> : null}
          {canApproveTasks ? <Button variant="filled" onClick={(e: Event) => { e.stopPropagation(); void approveTasks(); }}>Approve task list</Button> : null}
        </div>
      </div>
      {authed.value && spec.status !== "pending" ? (
        <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}><BreakdownButton nodeId={spec.id} /></div>
      ) : null}
    </div>
  );
}
