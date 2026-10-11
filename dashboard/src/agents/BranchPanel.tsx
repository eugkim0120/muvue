import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { openNode } from "../router";
import { refresh, toast } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { useAction } from "../ui/useAction";
import { STATE_CLASS, spendLabel, stateLabel, type BranchRow } from "./agentsData";

export function BranchPanel({ row, onClose }: { row: BranchRow; onClose: () => void }) {
  const w = row.worktree;
  const s = row.session;
  const stopA = useAction();
  const [details, setDetails] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  async function stop() {
    if (!s) return;
    setConfirmStop(false);
    const ok = await stopA.run(() => post(routes.sessionStop(s.id)));
    if (ok) { toast("stop sent to the agent"); refresh(); }
  }

  if (confirmStop) {
    return (
      <Confirm
        title="Stop this agent?"
        body="muvue sends SIGTERM to the agent's process group. The run stops and the task goes back to ready; this run does not retry it."
        confirmLabel="Stop agent"
        danger
        onConfirm={stop}
        onCancel={() => setConfirmStop(false)}
      />
    );
  }

  return (
    <Sheet title={w.branch ?? w.path} onClose={onClose}>
      <div class="stack">
        <section class="stack tight">
          <h3>Session</h3>
          {s ? (
            <p class="row">
              <span class={"pill " + STATE_CLASS[s.state]}>{stateLabel(s.state)}</span>
              <span>{s.agent} · {s.origin === "launched" ? "started by muvue" : "started by hand"} · {s.last_action ?? "no action yet"}</span>
            </p>
          ) : <p class="muted">No agent is working here.</p>}
          {s && details ? (
            <dl class="kv">
              <dt>id</dt><dd>{s.id}</dd>
              <dt>directory</dt><dd>{s.cwd}</dd>
              <dt>transcript</dt><dd>{s.transcript_path ?? "—"}</dd>
              <dt>started</dt><dd>{s.started_at}</dd>
              <dt>last event</dt><dd>{s.last_event_at}</dd>
              <dt>files touched</dt><dd>{s.touches.join(", ") || "—"}</dd>
              {s.spend ? <><dt>spend</dt><dd>{spendLabel(s.spend)}</dd></> : null}
            </dl>
          ) : null}
        </section>
        <section class="stack tight">
          <h3>Planned files</h3>
          {w.node_id === null ? <p class="muted">Off-plan: no task, so no planned files.</p> : w.planned.length === 0 ? (
            <p class="muted">The task names no files.</p>
          ) : (
            <ul class="planned">
              {w.planned.map((p) => <li><span class="mono">{p.glob}</span><span class="caption planned-state">{p.touched ? "changed" : "not yet"}</span></li>)}
            </ul>
          )}
          {w.changed_paths.length ? (
            <>
              <h3>Changed files</h3>
              <ul class="planned">{w.changed_paths.map((p) => <li class="mono">{p}</li>)}</ul>
            </>
          ) : null}
          {w.error ? <div class="callout danger">{w.error}</div> : null}
        </section>
        <section class="stack tight">
          <h3>Overlap</h3>
          <p class="muted">No overlap checks yet.</p>
        </section>
        <div class="row">
          {s ? <Button onClick={() => setDetails((d) => !d)}>{details ? "Hide session" : "Open session"}</Button> : null}
          {w.node_id !== null ? <Button onClick={() => { onClose(); openNode(w.node_id!); }}>Open diff</Button> : null}
          {s && s.origin === "launched" ? (
            <Button variant="danger" busy={stopA.busy} busyLabel="Stopping…" onClick={() => setConfirmStop(true)}>Stop agent</Button>
          ) : null}
        </div>
        {stopA.error ? <div class="callout danger">{stopA.error}</div> : null}
      </div>
    </Sheet>
  );
}
