import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import { markLaunched } from "../project/activity";

function AgentRow({ nodeId, projectId, agent, onStarted }: { nodeId: number; projectId: number; agent: string; onStarted: () => void }) {
  const a = useAction();
  async function start() {
    const ok = await a.run(async () => {
      const r = await post<{ spawned: { pid: number; log: string } }>(routes.nodeStart(nodeId, agent));
      toast(`started runner pid ${r.spawned.pid}`);
    });
    if (ok) { markLaunched({ kind: "run", nodeId, projectId, label: `#${nodeId}` }, null); refresh(); onStarted(); }
  }
  return (
    <div class="stack tight">
      <Button variant="outline" busy={a.busy} busyLabel="Starting…" onClick={start}>{agent}</Button>
      {a.error ? <div class="callout danger">{a.error}</div> : null}
    </div>
  );
}

export function StartPicker({ nodeId, projectId, onClose, onStarted }: { nodeId: number; projectId: number; onClose: () => void; onStarted: () => void }) {
  const [agents, setAgents] = useState<string[] | null>(null);
  useEffect(() => { api<{ agents: string[] }>(routes.agents()).then((r) => setAgents(r.agents), toastError); }, []);
  return (
    <div class="card stack tight">
      <div class="caption">Agent from config.toml [agents.*]</div>
      {agents === null ? <p class="muted">loading…</p> : agents.length ? (
        <div class="list stack tight">{agents.map((a) => <AgentRow key={a} nodeId={nodeId} projectId={projectId} agent={a} onStarted={onStarted} />)}</div>
      ) : <p class="muted">no agents configured</p>}
      <div class="actions"><Button variant="plain" onClick={onClose}>Cancel</Button></div>
    </div>
  );
}
