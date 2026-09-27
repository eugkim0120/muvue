import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { projectId, refreshTick, toastError, type NodeRow } from "../state";
import { Segmented } from "../ui/Segmented";
import { Empty } from "../ui/Empty";
import { Diagram } from "./Diagram";
import { TaskList } from "./TaskList";
import { SpecCard, type SpecNode } from "./SpecCard";
import type { Graph } from "./layout";

const isPhone = () => typeof window !== "undefined" && window.innerWidth < 900;

// The diagram shows only tasks — the spec is already shown by <SpecCard>
// above it — so spec nodes are dropped, and with them any edge that would
// otherwise dangle off a node layout() never laid out.
export function buildTaskGraph(graph: Graph): Graph {
  const tasks = graph.nodes.filter((n) => n.kind !== "spec");
  const taskIds = new Set(tasks.map((n) => n.id));
  const edges = graph.edges.filter((e) => taskIds.has(e.from) && taskIds.has(e.to));
  return { nodes: tasks, edges };
}

export function PlanPage() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [specs, setSpecs] = useState<SpecNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState(isPhone() ? "list" : "diagram");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const g = await api<Graph>(routes.graph(projectId.value));
        const specRows = g.nodes.filter((n) => n.kind === "spec");
        const full = await Promise.all(specRows.map((s) => api<{ node: SpecNode }>(routes.node(s.id)).then((d) => d.node)));
        if (!alive) return;
        setGraph(g); setSpecs(full); setError(null);
      } catch (e) { if (alive) { setError(e instanceof Error ? e.message : String(e)); toastError(e); } }
    })();
    return () => { alive = false; };
  }, [projectId.value, refreshTick.value]);

  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!graph) return <div class="page"><p class="muted">loading…</p></div>;
  const tasks = graph.nodes.filter((n) => n.kind !== "spec");
  const taskGraph = buildTaskGraph(graph);
  return (
    <div class="page stack">
      <h1 class="page-title">Plan</h1>
      {specs.length ? specs.map((s) => <SpecCard spec={s} taskCount={tasks.length} />) : <Empty text="No spec yet. Create one with muvue spec <project>." />}
      {tasks.length ? (
        <>
          <div class="row between">
            <h2>Tasks · {tasks.length}</h2>
            <Segmented options={[{ value: "diagram", label: "Diagram" }, { value: "list", label: "List" }]} value={mode} onChange={setMode} />
          </div>
          {mode === "diagram" ? <Diagram graph={taskGraph} /> : <TaskList nodes={tasks} />}
        </>
      ) : specs.length ? <Empty text="No tasks yet. They appear here once the spec is decomposed." /> : null}
    </div>
  );
}
