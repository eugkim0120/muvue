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
import { SubmitSpecForm } from "../canvas/SpecRoot";
import { StatusLine } from "./StatusLine";
import { AgentsSheet } from "./AgentsSheet";
import { NewProject } from "./NewProject";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";
import { useAction } from "../ui/useAction";
import { useActivity, activityItems, markLaunched, dismiss, dismissedKeys, launches, planningNodeIds, type LogRef } from "./activity";
import { ActivityBar } from "./ActivityBar";
import { LogSheet } from "./LogSheet";
import { NextStepBar } from "./NextStepBar";
import { nextStep, runBlockReason, type NextStepInput } from "./nextStep";

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
    return cardsFromInbox(inboxQ.data, { revisions: revisionsQ.data ?? [], nodesById, projectId: pid })
      .filter((c) => c.projectId === null || c.projectId === pid);
  }, [inboxQ.data, revisionsQ.data, nodesById, pid]);
  const needsYou = new Set(cards.map((c) => c.nodeId).filter((id): id is number => id !== null));

  if (!projects.value.length) return <NewProject />;
  if (!p) return <NewProject />;

  const done = (nodesQ.data ?? []).filter((n) => n.status === "done" && n.kind === "task").length;
  const total = (nodesQ.data ?? []).filter((n) => n.kind === "task").length;
  const working = (nodesQ.data ?? []).filter((n) => n.status === "in_progress").length;
  const hasFakeAgent = (graphQ.data?.nodes ?? []).some((n) => n.agent === "fake");

  const taskAndSubtaskNodes = (nodesQ.data ?? []).filter((n) => n.kind === "task" || n.kind === "subtask");
  const readyCount = taskAndSubtaskNodes.filter((n) => n.status === "ready").length;
  const workingCount = taskAndSubtaskNodes.filter((n) => n.status === "in_progress").length;
  const reviewCount = taskAndSubtaskNodes.filter((n) => n.status === "review").length;
  const step = nextStep({
    phase: p.phase,
    spec: data?.spec ? { id: data.spec.id, status: data.spec.status } : null,
    taskCount: total,
    readyCount,
    workingCount,
    reviewCount,
    doneCount: done,
    planning: data?.spec ? planningNodeIds(activity, launches.value).has(data.spec.id) : false,
  } satisfies NextStepInput);
  const reason = runBlockReason(step);

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
          <Button variant="filled" busy={runA.busy} busyLabel="Starting…" disabled={!!reason} onClick={run}>▶ Run tasks</Button>
          {reason ? <span class="caption" data-run-reason>{reason}</span> : null}
        </div>
      </div>
      {runA.error ? <div class="callout danger" style={{ margin: "0 16px" }}>{runA.error}</div> : null}
      {hasFakeAgent ? <div class="callout" data-fake-notice style={{ margin: "0 16px" }}>Tasks here are routed to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</div> : null}
      <NextStepBar step={step} authed={authed.value} projectId={pid!} specId={data?.spec?.id ?? null} activity={activity} onAddTask={() => setAddingTask(true)} />
      <ActivityBar items={items} now={now} onDismiss={dismiss} onOpenLog={setOpenLog} />
      <button type="button" class="status-line-btn" onClick={() => setAgentsSheet(true)}>
        <StatusLine phase={p.phase} done={done} total={total} working={working} />
      </button>
      {authed.value && addingTask && data?.spec ? (
        <div style={{ padding: "0 16px 16px" }}>
          <AddForm parentId={data.spec.id} kind="task" candidates={data.tasks} onClose={() => setAddingTask(false)} />
        </div>
      ) : null}
      {graphQ.error || nodesQ.error ? <div class="callout danger">{graphQ.error || nodesQ.error}</div> : !data ? <Empty text="loading…" /> : step.id === "write_spec" ? (
        <div style={{ padding: "0 16px 16px" }}>
          <SubmitSpecForm projectId={pid!} />
        </div>
      ) : isPhone() ? (
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
