import { signal } from "@preact/signals";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, ApiError } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, refreshTick, toast } from "../state";

export type ActiveProc = { kind: "run" | "breakdown"; pid: number; node_id: number | null; started_at: string };
export type BreakdownOutcome = {
  node_id: number; event_id: number; type: "breakdown.started" | "breakdown.finished" | "breakdown.failed";
  ts: string; agent: string | null; reason: string | null; created: number[] | null;
};
export type Working = { node_id: number; title: string; agent: string | null };
export type Activity = { active: ActiveProc[]; breakdowns: BreakdownOutcome[]; working: Working[] };
export type Launch = { kind: "run" | "breakdown"; nodeId: number | null; projectId: number | null; label: string; afterEventId: number; at: number };
export type LogRef = { kind: "node"; nodeId: number } | { kind: "project"; projectId: number };
export type ActivityItem =
  | { tone: "busy"; key: string; text: string; startedAt: number; log: LogRef | null }
  | { tone: "error"; key: string; text: string; log: LogRef | null };

export const LAUNCH_TIMEOUT_MS = 15000;
const STORAGE_KEY = "muvue.dismissedActivity";
// Bound the dismissed-key set so localStorage doesn't grow unbounded across
// a machine's lifetime of dismissed errors; a simple truncation to the most
// recently dismissed keys on write is enough since older ones are no longer
// useful once their source event has scrolled out of the activity feed.
const MAX_DISMISSED_KEYS = 200;

function loadDismissed(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as string[]); } catch { return new Set(); }
}
export const dismissedKeys = signal<Set<string>>(loadDismissed());

// A dismissed timeout error's key encodes the launch it came from, so
// dismissing it can also drop that launch from `launches` — otherwise the
// launch would sit there forever with nothing left to display it, and
// useActivity's "poll while any unconfirmed launch exists" check would never
// go false again.
const TIMEOUT_KEY_RE = /^launch-timeout:(run|breakdown):(-?\d+|null):(-?\d+)$/;
function timeoutKey(l: Launch): string {
  return `launch-timeout:${l.kind}:${l.nodeId}:${l.at}`;
}
export function dismiss(key: string): void {
  let next = new Set(dismissedKeys.value); next.add(key);
  if (next.size > MAX_DISMISSED_KEYS) next = new Set([...next].slice(-MAX_DISMISSED_KEYS));
  dismissedKeys.value = next;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...next])); } catch { /* per-viewer convenience only; the in-memory set still hides it */ }
  const m = TIMEOUT_KEY_RE.exec(key);
  if (m) {
    const [, kind, nodeIdStr, atStr] = m;
    const nodeId = nodeIdStr === "null" ? null : Number(nodeIdStr);
    const at = Number(atStr);
    launches.value = launches.value.filter((l) => !(l.kind === kind && l.nodeId === nodeId && l.at === at));
  }
}

export const launches = signal<Launch[]>([]);

export function latestEventId(a: Activity | null, nodeId: number | null): number {
  return a?.breakdowns.find((b) => b.node_id === nodeId)?.event_id ?? 0;
}
export function markLaunched(l: Omit<Launch, "at" | "afterEventId">, a: Activity | null): void {
  const launch: Launch = { ...l, at: Date.now(), afterEventId: latestEventId(a, l.nodeId) };
  launches.value = [...launches.value.filter((x) => !(x.kind === l.kind && x.nodeId === l.nodeId && x.projectId === l.projectId)), launch];
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
  // A launch belonging to a different project (this client's `launches`
  // signal is global, not scoped to a project) must never surface here.
  for (const l of launched) {
    if (l.projectId !== null && l.projectId !== projectId) continue;
    if (confirmed(l, a)) continue;
    const log: LogRef = l.nodeId !== null ? { kind: "node", nodeId: l.nodeId } : { kind: "project", projectId };
    if (now - l.at < LAUNCH_TIMEOUT_MS) {
      items.push({ tone: "busy", key: `launch:${l.kind}:${l.nodeId}`, text: `Starting ${l.label}…`, startedAt: l.at, log: null });
    } else {
      const key = timeoutKey(l);
      if (dismissed.has(key)) continue;
      items.push({
        tone: "error",
        key,
        text: `${l.label} did not start: nothing was reported within 15 seconds. Check the log.`,
        log,
      });
    }
  }

  // Rule 4: persistent breakdown failures, until dismissed. The key
  // includes the project id and timestamp (not just event_id) since event
  // ids are small per-database integers shared across every repo served on
  // the same `muvue serve` port -- an event_id-only key would let one
  // repo's dismissal hide a different repo's failure.
  for (const b of activity.breakdowns) {
    if (b.type !== "breakdown.failed") continue;
    const key = `bd-fail:${projectId}:${b.event_id}:${b.ts}`;
    if (dismissed.has(key)) continue;
    const title = titles[b.node_id] ?? "#" + b.node_id;
    items.push({ tone: "error", key, text: `Planning “${title}” failed: ${b.reason ?? "no reason recorded"}`, log: { kind: "node", nodeId: b.node_id } });
  }

  return items;
}

// Task 9's placeholder: nodes with a breakdown running now, or just launched
// and not yet confirmed or timed out. `projectId` scopes `launched`, which
// is a global signal, so a launch against a different project never marks
// one of this project's nodes as planning.
export function planningNodeIds(a: Activity | null, launched: Launch[], projectId: number | null): Set<number> {
  const ids = new Set<number>();
  for (const p of a?.active ?? []) {
    if (p.kind === "breakdown" && p.node_id !== null) ids.add(p.node_id);
  }
  const now = Date.now();
  for (const l of launched) {
    if (l.kind !== "breakdown" || l.nodeId === null) continue;
    if (l.projectId !== null && l.projectId !== projectId) continue;
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
    // The activity route is guarded: a guest would only get a 403.
    if (projectId === null || !authed.value) { setActivity(null); return; }
    let alive = true;
    api<Activity>(routes.projectActivity(projectId), { background: true }).then((a) => {
      if (!alive) return;
      // `launches` is a global signal, not scoped per project, so a launch
      // fired against a different project must never be confirmed, pruned,
      // or toasted about using this project's `a` -- it would falsely
      // surface (or falsely dismiss) as this project's activity.
      for (const l of launches.value) {
        if (l.projectId !== null && l.projectId !== projectId) continue;
        if (l.kind !== "breakdown" || !confirmed(l, a)) continue;
        const outcome = a.breakdowns.find((b) => b.node_id === l.nodeId);
        if (outcome?.type === "breakdown.finished" && outcome.event_id > l.afterEventId) { toast(`Added ${outcome.created?.length ?? 0} tasks`); refresh(); }
      }
      launches.value = launches.value.filter((l) => {
        if (l.projectId !== null && l.projectId !== projectId) return true;
        return !confirmed(l, a) || (l.kind === "breakdown" && a.active.some((p) => p.node_id === l.nodeId));
      });
      prev.current = a;
      setActivity(a);
    }, (e) => {
      if (!alive) return;
      // Signed out (or the session lapsed): the sign-in strip already says so.
      if (e instanceof ApiError && e.auth) return;
      toast(e instanceof Error ? e.message : String(e), "error");
    });
    return () => { alive = false; };
  }, [projectId, refreshTick.value, tick, authed.value]);
  // A launch past LAUNCH_TIMEOUT_MS already has its (dismissable) error
  // item; it will never confirm, so it must not keep the poll alive forever.
  // A launch against a different project must not extend this project's
  // polling either.
  const polling = !!activity && (activity.active.length > 0 || launches.value.some((l) => (l.projectId === null || l.projectId === projectId) && Date.now() - l.at < LAUNCH_TIMEOUT_MS));
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => setTick((n) => n + 1), 1500);
    return () => clearInterval(t);
  }, [polling]);
  return activity;
}
