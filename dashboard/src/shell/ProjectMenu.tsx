import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast, toastError } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { Pill } from "../ui/Pill";
import { CloseSheet } from "../project/CloseSheet";

export function ProjectMenu({ onClose }: { onClose: () => void }) {
  const [confirmPause, setConfirmPause] = useState(false);
  const [closing, setClosing] = useState(false);
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
  async function resume() {
    if (!p) return;
    try { await post(routes.projectResume(p.id)); toast("resumed"); refresh(); onClose(); } catch (e) { toastError(e); }
  }

  if (closing && p) return <CloseSheet projectId={p.id} onClose={() => { setClosing(false); onClose(); }} />;
  if (confirmPause) {
    return <Confirm title="Pause project?" body="Running agents are stopped and their tasks go back to ready." confirmLabel="Pause" danger onConfirm={pause} onCancel={() => setConfirmPause(false)} />;
  }
  return (
    <Sheet title="Project" onClose={onClose}>
      <div class="stack">
        <div class="list">
          {projects.value.map((pr) => (
            <button type="button" class={"list-row" + (pr.id === projectId.value ? " on" : "")} onClick={() => { projectId.value = pr.id; refresh(); onClose(); }}>
              <span class="grow"><span class="title">#{pr.id} {pr.goal}</span></span>
              <Pill status={pr.phase} />
            </button>
          ))}
        </div>
        {authed.value && p ? (
          <div class="actions">
            {p.phase !== "paused" && p.phase !== "closed" ? <Button variant="danger" onClick={() => setConfirmPause(true)}>Pause</Button> : null}
            {p.phase === "paused" ? <Button variant="filled" onClick={resume}>Resume</Button> : null}
            {p.phase !== "closed" ? <Button onClick={() => setClosing(true)}>Close project…</Button> : null}
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}
