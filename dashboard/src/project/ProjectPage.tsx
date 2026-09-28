import { useEffect, useMemo, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { useApi } from "../hooks";
import { authed, currentProject, projectId, projects, refreshTick, toast } from "../state";
import { buildCanvasData, type Graph, type FullNode } from "../canvas/canvasData";
import { Canvas } from "../canvas/Canvas";
import { PhoneFlow } from "../canvas/PhoneFlow";
import { CardRail } from "../cards/CardRail";
import { cardsFromInbox, type Inbox, type Revision, type NodesById } from "../cards/cardsFromInbox";
import { AddForm } from "../canvas/AddForm";
import { StatusLine } from "./StatusLine";
import { AgentsSheet } from "./AgentsSheet";
import { NewProject } from "./NewProject";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";
import { useAction } from "../ui/useAction";
import { useActivity, activityItems, markLaunched, dismiss, dismissedKeys, launches, type LogRef } from "./activity";
import { ActivityBar } from "./ActivityBar";
import { LogSheet } from "./LogSheet";

const isPhone = () => typeof window !== "undefined" && window.innerWidth < 900;

export function ProjectPage() {
  const [agentsSheet, setAgentsSheet] = useState(false);
  const [addingTask, setAddingTask] = useState(false);
  const [openLog, setOpenLog] = useState<LogRef | null>(null);
  const [now, setNow] = useState(Date.now());
  const p = currentProject.value;
  const pid = projectId.value;
  const runA = useAction();
  const activity = useActivity(pid);

  const graphQ = useApi<Graph>(() => api(routes.graph(pid)), [pid, refreshTick.value]);
  const nodesQ = useApi<FullNode[]>(() => api(routes.nodes(pid)), [pid, refreshTick.value]);
  const inboxQ = useApi<Inbox>(() => api(routes.inbox()), [refreshTick.value]);
  const revisionsQ = useApi<Revision[]>(() => (pid ? api(routes.revisions(pid)) : Promise.resolve([])), [pid, refreshTick.value]);
  const allNodesQ = useApi<FullNode[]>(() => api(routes.nodes(null)), [refreshTick.value]);

  const data = useMemo(() => (graphQ.data && nodesQ.data ? buildCanvasData(graphQ.data, nodesQ.data) : null), [graphQ.data, nodesQ.data]);

  const titles: Record<number, string> = useMemo(() => Object.fromEntries((nodesQ.data ?? []).map((n) => [n.id, n.title])), [nodesQ.data]);
  const items = useMemo(
    () => activityItems(activity, launches.value, now, titles, dismissedKeys.value, pid ?? 0),
    [activity, launches.value, now, titles, dismissedKeys.value, pid],
  );
  const hasBusy = items.some((i) => i.tone === "busy");
  useEffect(() => {
    if (!hasBusy) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [hasBusy]);

  const nodesById: NodesById = useMemo(() => Object.fromEntries((allNodesQ.data ?? []).map((n) => [n.id, { title: n.title, project_id: n.project_id }])), [allNodesQ.data]);
  const cards = useMemo(() => {
    if (!inboxQ.data || !pid) return [];
    return cardsFromInbox(inboxQ.data, { spec: data?.spec ?? null, taskCount: data?.tasks.length ?? 0, projectPhase: p?.phase ?? "planning", revisions: revisionsQ.data ?? [], nodesById, projectId: pid })
      .filter((c) => c.projectId === null || c.projectId === pid);
  }, [inboxQ.data, data, p?.phase, revisionsQ.data, nodesById, pid]);
  const needsYou = new Set(cards.map((c) => c.nodeId).filter((id): id is number => id !== null));

  if (!projects.value.length) return <NewProject />;
  if (!p) return <NewProject />;

  const done = (nodesQ.data ?? []).filter((n) => n.status === "done" && n.kind === "task").length;
  const total = (nodesQ.data ?? []).filter((n) => n.kind === "task").length;
  const busyAgents = new Set((nodesQ.data ?? []).filter((n) => n.status === "in_progress").map((n) => n.owner)).size;

  async function run() {
    if (!pid) return;
    const ok = await runA.run(() => post(routes.projectRun(pid), {}));
    if (ok) { toast("run started"); markLaunched({ kind: "run", nodeId: null, label: "the run" }, activity); }
  }

  return (
    <div class="page-canvas">
      <div class="row between" style={{ padding: "16px 16px 0" }}>
        <h1 class="page-title">{p.goal}</h1>
        <div class="row">
          {authed.value && data?.spec && data.spec.status !== "pending" ? (
            <Button variant="outline" onClick={() => setAddingTask((a) => !a)}>+ Task</Button>
          ) : null}
          <Button variant="filled" busy={runA.busy} busyLabel="Starting…" onClick={run}>▶ Run</Button>
        </div>
      </div>
      {runA.error ? <div class="callout danger" style={{ margin: "0 16px" }}>{runA.error}</div> : null}
      <ActivityBar items={items} now={now} onDismiss={dismiss} onOpenLog={setOpenLog} />
      <button type="button" class="status-line-btn" onClick={() => setAgentsSheet(true)}>
        <StatusLine phase={p.phase} done={done} total={total} busyAgents={busyAgents} spend={0} budget={1} />
      </button>
      {authed.value && addingTask && data?.spec ? (
        <div style={{ padding: "0 16px 16px" }}>
          <AddForm parentId={data.spec.id} kind="task" candidates={data.tasks} onClose={() => setAddingTask(false)} />
        </div>
      ) : null}
      {graphQ.error || nodesQ.error ? <div class="callout danger">{graphQ.error || nodesQ.error}</div> : !data ? <Empty text="loading…" /> : isPhone() ? (
        <div class="stack">
          <CardRail cards={cards} />
          <div style={{ padding: "0 16px 16px" }}>
            <PhoneFlow data={data} projectId={pid!} projectPhase={p.phase} needsYou={needsYou} activity={activity} />
          </div>
        </div>
      ) : (
        <div class="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
          <div style={{ flex: 1, minWidth: 0, padding: "0 16px 16px" }}>
            <Canvas data={data} projectId={pid!} projectPhase={p.phase} needsYou={needsYou} activity={activity} />
          </div>
          <CardRail cards={cards} />
        </div>
      )}
      {agentsSheet ? <AgentsSheet projectId={pid!} onClose={() => setAgentsSheet(false)} /> : null}
      {openLog ? <LogSheet log={openLog} onClose={() => setOpenLog(null)} /> : null}
    </div>
  );
}
