import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";

export function StartPicker({ nodeId, onClose, onStarted }: { nodeId: number; onClose: () => void; onStarted: () => void }) {
  const [agents, setAgents] = useState<string[] | null>(null);
  useEffect(() => { api<{ agents: string[] }>(routes.agents()).then((r) => setAgents(r.agents), toastError); }, []);
  async function start(agent: string) {
    try {
      const r = await post<{ spawned: { pid: number; log: string } }>(routes.nodeStart(nodeId, agent));
      toast(`started runner pid ${r.spawned.pid}`);
      refresh(); onStarted();
    } catch (e) { toastError(e); }
  }
  return (
    <div class="card stack tight">
      <div class="caption">Agent from config.toml [agents.*]</div>
      {agents === null ? <p class="muted">loading…</p> : agents.length ? (
        <div class="list">{agents.map((a) => <button type="button" class="list-row" onClick={() => start(a)}><span class="grow title">{a}</span></button>)}</div>
      ) : <p class="muted">no agents configured</p>}
      <div class="actions"><Button variant="plain" onClick={onClose}>Cancel</Button></div>
    </div>
  );
}
