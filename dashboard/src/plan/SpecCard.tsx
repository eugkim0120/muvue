import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, currentProject, refresh, toast, toastError, type NodeRow } from "../state";
import { navigate } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";

export type SpecNode = NodeRow & { body_md: string | null };

export function SpecCard({ spec, taskCount }: { spec: SpecNode; taskCount: number }) {
  const p = currentProject.value;
  const canApproveSpec = authed.value && spec.status === "pending";
  const canApproveTasks = authed.value && spec.status !== "pending" && taskCount > 0 && p?.phase === "planning";
  const preview = (spec.body_md || "").split("\n").filter((l) => l.trim()).slice(0, 3).join("\n");

  async function approveSpec() {
    try { await post(routes.nodeApprove(spec.id), { target: "spec" }); toast("spec approved"); refresh(); } catch (e) { toastError(e); }
  }
  async function approveTasks() {
    if (!p) return;
    try { await post(routes.nodeApprove(p.id), { target: "gate2" }); toast("task list approved; criteria frozen"); refresh(); } catch (e) { toastError(e); }
  }

  return (
    <section class="card stack">
      <div class="row between">
        <h2>#{spec.id} {spec.title}</h2>
        <Pill status={spec.status} />
      </div>
      {preview ? <pre class="spec-preview">{preview}</pre> : null}
      <div class="actions">
        {canApproveSpec ? <Button variant="filled" onClick={approveSpec}>Approve spec</Button> : null}
        {canApproveTasks ? <Button variant="filled" onClick={approveTasks}>Approve task list</Button> : null}
        <Button variant="plain" onClick={() => navigate("#/spec/" + spec.id)}>Read spec</Button>
      </div>
      {canApproveTasks ? <div class="caption">Approving freezes each task's acceptance criteria.</div> : null}
    </section>
  );
}
