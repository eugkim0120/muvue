import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { Sheet } from "../ui/Sheet";

type AgentStatus = { name: string; roles: string[]; current: { node_id: number; title: string; elapsed_s: number } | null; runs_today: number; spend: number; budget: number; last_error: string | null };

export function AgentsSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ agents: AgentStatus[] }>(routes.agentsStatus(projectId)).then(
      (r) => { if (!alive) return; setAgents(r.agents); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [projectId]);
  return (
    <Sheet title="Agents" onClose={onClose}>
      {error ? <div class="callout danger">{error}</div> : !agents ? <p class="muted">loading…</p> : (
        <div class="list">
          {agents.map((a) => (
            <div class="list-row" style={{ cursor: "default" }}>
              <span class="grow">
                <span class="title">{a.name} <span class="caption">{a.roles.join(", ")}</span></span>
                <span class="caption" style={{ display: "block" }}>{a.current ? `#${a.current.node_id} ${a.current.title} · ${a.current.elapsed_s}s` : "idle"}</span>
                <span class="caption" style={{ display: "block" }}>{a.runs_today} runs today · ${a.spend.toFixed(2)} of ${a.budget.toFixed(2)}</span>
                {a.last_error ? <span class="caption danger" style={{ display: "block" }}>{a.last_error}</span> : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  );
}
