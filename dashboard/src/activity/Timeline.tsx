import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { projectId, refreshTick } from "../state";
import { openNode } from "../router";
import { Empty } from "../ui/Empty";

export type Ev = { id: number; ts: string; type: string; actor: string; actor_evidence: string; node_id: number | null };
const FAMILIES = ["task", "review", "runner", "commit", "question", "project"] as const;
type Family = (typeof FAMILIES)[number] | "other";
const PREFIX: Record<string, Family> = { node: "task", review: "review", runner: "runner", commit: "commit", question: "question", project: "project" };

export function familyOf(type: string): Family {
  return PREFIX[type.split(".")[0] ?? ""] ?? "other";
}

export function groupByDay(events: Ev[]): { day: string; events: Ev[] }[] {
  const byDay = new Map<string, Ev[]>();
  for (const ev of [...events].sort((a, b) => b.ts.localeCompare(a.ts) || b.id - a.id)) {
    const day = ev.ts.slice(0, 10);
    (byDay.get(day) ?? byDay.set(day, []).get(day)!).push(ev);
  }
  return [...byDay.entries()].map(([day, evs]) => ({ day, events: evs }));
}

const CHIPS: { value: Family | "all"; label: string }[] = [
  { value: "all", label: "All" }, { value: "task", label: "Tasks" }, { value: "review", label: "Reviews" }, { value: "runner", label: "Runner" },
  { value: "commit", label: "Commits" }, { value: "question", label: "Questions" }, { value: "project", label: "Project" },
];

export function Timeline() {
  const [events, setEvents] = useState<Ev[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chip, setChip] = useState<Family | "all">("all");
  useEffect(() => {
    if (projectId.value == null) { setEvents([]); return; }
    let alive = true;
    api<Ev[]>(routes.events(projectId.value)).then(
      (e) => { if (!alive) return; setEvents(e); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [projectId.value, refreshTick.value]);
  if (error) return <div class="callout danger">{error}</div>;
  if (!events) return <p class="muted">loading…</p>;
  const shown = chip === "all" ? events : events.filter((e) => familyOf(e.type) === chip);
  return (
    <div class="stack">
      <div class="chips">{CHIPS.map((c) => <button type="button" class={"chip" + (chip === c.value ? " on" : "")} onClick={() => setChip(c.value)}>{c.label}</button>)}</div>
      {shown.length ? groupByDay(shown).map((g) => (
        <section>
          <h3 class="group-head sticky">{g.day}</h3>
          <div class="list">
            {g.events.map((ev) => (
              <button type="button" class="list-row" onClick={() => { if (ev.node_id) openNode(ev.node_id); }} disabled={!ev.node_id}>
                <span class="grow">
                  <span class="title">{ev.type}{ev.node_id ? ` · #${ev.node_id}` : ""}</span>
                  <span class="caption" style={{ display: "block" }}>{ev.actor} ({ev.actor_evidence}) · {ev.ts.slice(11, 19)}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )) : <Empty text="No events yet." />}
    </div>
  );
}
