import { signal } from "@preact/signals";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refresh, refreshTick, toast } from "../state";

export type ActiveProc = { kind: "run" | "breakdown"; pid: number; node_id: number | null; started_at: string };
export type BreakdownOutcome = {
  node_id: number; event_id: number; type: "breakdown.started" | "breakdown.finished" | "breakdown.failed";
  ts: string; agent: string | null; reason: string | null; created: number[] | null;
};
export type Working = { node_id: number; title: string; agent: string | null };
export type Activity = { active: ActiveProc[]; breakdowns: BreakdownOutcome[]; working: Working[] };
export type Launch = { kind: "run" | "breakdown"; nodeId: number | null; label: string; afterEventId: number; at: number };
export type LogRef = { kind: "node"; nodeId: number } | { kind: "project"; projectId: number };
export type ActivityItem =
  | { tone: "busy"; key: string; text: string; startedAt: number; log: LogRef | null }
  | { tone: "error"; key: string; text: string; log: LogRef | null };

export const LAUNCH_TIMEOUT_MS = 15000;
const STORAGE_KEY = "muvue.dismissedActivity";

function loadDismissed(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as string[]); } catch { return new Set(); }
}
export const dismissedKeys = signal<Set<string>>(loadDismissed());
export function dismiss(key: string): void {
  const next = new Set(dismissedKeys.value); next.add(key); dismissedKeys.value = next;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...next])); } catch { /* per-viewer convenience only; the in-memory set still hides it */ }
}

export const launches = signal<Launch[]>([]);

export function latestEventId(a: Activity | null, nodeId: number | null): number {
  return a?.breakdowns.find((b) => b.node_id === nodeId)?.event_id ?? 0;
}
export function markLaunched(l: Omit<Launch, "at" | "afterEventId">, a: Activity | null): void {
  const launch: Launch = { ...l, at: Date.now(), afterEventId: latestEventId(a, l.nodeId) };
  launches.value = [...launches.value.filter((x) => !(x.kind === l.kind && x.nodeId === l.nodeId)), launch];
}
function confirmed(l: Launch, a: Activity | null): boolean {
  if (!a) return false;
  if (l.kind === "run") return a.active.some((p) => p.kind === "run");
  return a.active.some((p) => p.kind === "breakdown" && p.node_id === l.nodeId) || latestEventId(a, l.nodeId) > l.afterEventId;
}

// Busy items keyed by rules 1-2 don't carry the server's `started_at` (phone
// and server clocks differ), so the elapsed-seconds display is anchored to
// the first tick this client actually saw the item, keyed the same way the
// item itself is keyed.
const firstSeen = new Map<string, number>();
function seenAt(key: string, now: number): number {
  if (!firstSeen.has(key)) firstSeen.set(key, now);
  return firstSeen.get(key)!;
}

export function activityItems(
  a: Activity | null,
  launched: Launch[],
  now: number,
  titles: Record<number, string>,
  dismissed: Set<string>,
  projectId: number,
): ActivityItem[] {
  const items: ActivityItem[] = [];
  const activity: Activity = a ?? { active: [], breakdowns: [], working: [] };

  // Rule 1: each active breakdown process names its agent and node.
  for (const p of activity.active) {
    if (p.kind !== "breakdown" || p.node_id === null) continue;
    const nodeId = p.node_id;
    const key = `bd-run:${nodeId}`;
    const agent = activity.breakdowns.find((b) => b.node_id === nodeId)?.agent ?? "agent";
    const title = titles[nodeId] ?? "#" + nodeId;
    items.push({ tone: "busy", key, text: `✨ ${agent} is planning tasks for “${title}”`, startedAt: seenAt(key, now), log: { kind: "node", nodeId } });
  }

  // Rule 2: at most one item for a live run, listing who is working.
  if (activity.active.some((p) => p.kind === "run")) {
    const key = "run";
    const n = activity.working.length;
    let text = `▶ Running tasks · ${n} agent${n === 1 ? "" : "s"} working`;
    if (activity.working.length) text += ` — ${activity.working.map((w) => w.title).join(", ")}`;
    items.push({ tone: "busy", key, text, startedAt: seenAt(key, now), log: { kind: "project", projectId } });
  }

  // Rule 3: launches this client fired, until the server confirms or times out.
  for (const l of launched) {
    if (confirmed(l, a)) continue;
    if (now - l.at < LAUNCH_TIMEOUT_MS) {
      items.push({ tone: "busy", key: `launch:${l.kind}:${l.nodeId}`, text: `Starting ${l.label}…`, startedAt: l.at, log: null });
    } else {
      items.push({
        tone: "error",
        key: `launch-timeout:${l.kind}:${l.nodeId}:${l.at}`,
        text: `${l.label} did not start: nothing was reported within 15 seconds. Check the log.`,
        log: null,
      });
    }
  }

  // Rule 4: persistent breakdown failures, until dismissed.
  for (const b of activity.breakdowns) {
    if (b.type !== "breakdown.failed") continue;
    const key = `bd-fail:${b.event_id}`;
    if (dismissed.has(key)) continue;
    const title = titles[b.node_id] ?? "#" + b.node_id;
    items.push({ tone: "error", key, text: `Planning “${title}” failed: ${b.reason ?? "no reason recorded"}`, log: { kind: "node", nodeId: b.node_id } });
  }

  return items;
}

// Task 9's placeholder: nodes with a breakdown running now, or just launched
// and not yet confirmed or timed out.
export function planningNodeIds(a: Activity | null, launched: Launch[]): Set<number> {
  const ids = new Set<number>();
  for (const p of a?.active ?? []) {
    if (p.kind === "breakdown" && p.node_id !== null) ids.add(p.node_id);
  }
  const now = Date.now();
  for (const l of launched) {
    if (l.kind !== "breakdown" || l.nodeId === null) continue;
    if (confirmed(l, a)) continue;
    if (now - l.at < LAUNCH_TIMEOUT_MS) ids.add(l.nodeId);
  }
  return ids;
}

export function useActivity(projectId: number | null): Activity | null {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [tick, setTick] = useState(0);
  const prev = useRef<Activity | null>(null);
  useEffect(() => {
    if (projectId === null) return;
    let alive = true;
    api<Activity>(routes.projectActivity(projectId)).then((a) => {
      if (!alive) return;
      for (const l of launches.value) {
        if (l.kind !== "breakdown" || !confirmed(l, a)) continue;
        const outcome = a.breakdowns.find((b) => b.node_id === l.nodeId);
        if (outcome?.type === "breakdown.finished" && outcome.event_id > l.afterEventId) { toast(`Added ${outcome.created?.length ?? 0} tasks`); refresh(); }
      }
      launches.value = launches.value.filter((l) => !confirmed(l, a) || (l.kind === "breakdown" && a.active.some((p) => p.node_id === l.nodeId)));
      prev.current = a;
      setActivity(a);
    }, (e) => { if (alive) toast(e instanceof Error ? e.message : String(e), "error"); });
    return () => { alive = false; };
  }, [projectId, refreshTick.value, tick]);
  const polling = !!activity && (activity.active.length > 0 || launches.value.length > 0);
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => setTick((n) => n + 1), 1500);
    return () => clearInterval(t);
  }, [polling]);
  return activity;
}
