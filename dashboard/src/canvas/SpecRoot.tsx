import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast } from "../state";
import type { CanvasSpec } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";
import { BreakdownButton } from "./AddForm";
import { useAction } from "../ui/useAction";
import type { Activity } from "../project/activity";

function SubmitSpecForm({ projectId }: { projectId: number }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const a = useAction();
  async function submit(e: Event) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    const ok = await a.run(() => post(routes.projectSpec(projectId), { title, body_md: body }));
    if (ok) { toast("spec submitted"); refresh(); }
  }
  return (
    <form class="spec-root-card stack" onSubmit={submit}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one requirement per line" value={body} onInput={(e) => setBody((e.target as HTMLTextAreaElement).value)} />
      {a.error ? <div class="callout danger">{a.error}</div> : null}
      <div class="actions"><Button type="submit" variant="filled" busy={a.busy} busyLabel="Submitting…">Submit spec</Button></div>
    </form>
  );
}

export function SpecRoot({ spec, projectId, taskCount, projectPhase, activity }: { spec: CanvasSpec | null; projectId: number; taskCount: number; projectPhase: string; activity?: Activity | null }) {
  const approveSpecA = useAction();
  const approveTasksA = useAction();
  if (!spec) return authed.value ? <SubmitSpecForm projectId={projectId} /> : <div class="spec-root-card"><p class="muted">No spec yet.</p></div>;
  const canApproveSpec = authed.value && spec.status === "pending";
  const canApproveTasks = authed.value && spec.status !== "pending" && taskCount > 0 && projectPhase === "planning";

  async function approveSpec() {
    const ok = await approveSpecA.run(() => post(routes.nodeApprove(spec!.id), { target: "spec" }));
    if (ok) { toast("spec approved"); refresh(); }
  }
  async function approveTasks() {
    const ok = await approveTasksA.run(() => post(routes.nodeApprove(projectId), { target: "gate2" }));
    if (ok) { toast("task list approved; criteria frozen"); refresh(); }
  }

  return (
    <div class="spec-root-card stack" tabIndex={0} onClick={() => openNode(spec.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(spec.id); }}>
      <div class="row between">
        <span class="title grow">{spec.title}</span>
        <Pill status={spec.status} />
      </div>
      {spec.body_md ? <div class="caption">{purposeLine(spec.body_md)}</div> : null}
      {spec.agent ? <div class="caption">{`planned by ${spec.agent}`}</div> : null}
      <div class="row between">
        <div class="actions">
          {canApproveSpec ? <Button variant="filled" busy={approveSpecA.busy} busyLabel="Approving…" onClick={(e: Event) => { e.stopPropagation(); void approveSpec(); }}>Approve spec</Button> : null}
          {canApproveTasks ? <Button variant="filled" busy={approveTasksA.busy} busyLabel="Approving…" onClick={(e: Event) => { e.stopPropagation(); void approveTasks(); }}>Approve task list</Button> : null}
        </div>
      </div>
      {approveSpecA.error ? <div class="callout danger">{approveSpecA.error}</div> : null}
      {approveTasksA.error ? <div class="callout danger">{approveTasksA.error}</div> : null}
      {authed.value && spec.status !== "pending" ? (
        <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}><BreakdownButton nodeId={spec.id} activity={activity ?? null} /></div>
      ) : null}
    </div>
  );
}
