import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { Pill } from "../ui/Pill";
import { CloseSheet } from "../project/CloseSheet";
import { AgentsSheet } from "../project/AgentsSheet";
import { HistorySheet } from "../project/HistorySheet";
import { useAction } from "../ui/useAction";

export function ProjectMenu({ onClose }: { onClose: () => void }) {
  const [confirmPause, setConfirmPause] = useState(false);
  const [closing, setClosing] = useState(false);
  const [agents, setAgents] = useState(false);
  const [history, setHistory] = useState(false);
  const p = currentProject.value;
  const pauseA = useAction();
  const resumeA = useAction();

  async function pause() {
    if (!p) return;
    const ok = await pauseA.run(async () => {
      const r = await post<{ result?: { stopped_runners?: unknown[] }; stopped_runners?: unknown[] }>(routes.projectPause(p.id));
      const stopped = (r.result ?? r).stopped_runners ?? [];
      toast(`paused; stopped ${stopped.length} runner process(es)`);
    });
    if (ok) { refresh(); onClose(); }
  }
  async function resume() {
    if (!p) return;
    const ok = await resumeA.run(() => post(routes.projectResume(p.id)));
    if (ok) { toast("resumed"); refresh(); onClose(); }
  }

  if (agents && p) return <AgentsSheet projectId={p.id} onClose={() => { setAgents(false); onClose(); }} />;
  if (history) return <HistorySheet onClose={() => { setHistory(false); onClose(); }} />;
  if (closing && p) return <CloseSheet projectId={p.id} onClose={() => { setClosing(false); onClose(); }} />;
  if (confirmPause) {
    return (
      <Confirm
        title="Pause project?"
        body={<>Running agents are stopped and their tasks go back to ready.{pauseA.error ? <div class="callout danger">{pauseA.error}</div> : null}</>}
        confirmLabel={pauseA.busy ? "Pausing…" : "Pause"}
        danger
        onConfirm={pause}
        onCancel={() => setConfirmPause(false)}
      />
    );
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
          <div class="stack tight">
            <div class="actions">
              {p.phase !== "paused" && p.phase !== "closed" ? <Button variant="danger" onClick={() => setConfirmPause(true)}>Pause</Button> : null}
              {p.phase === "paused" ? <Button variant="filled" busy={resumeA.busy} busyLabel="Resuming…" onClick={resume}>Resume</Button> : null}
              {p.phase !== "closed" ? <Button onClick={() => setClosing(true)}>Close project…</Button> : null}
              <Button onClick={() => setAgents(true)}>Agents</Button>
              <Button onClick={() => setHistory(true)}>History</Button>
            </div>
            {resumeA.error ? <div class="callout danger">{resumeA.error}</div> : null}
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}
