import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick } from "../state";
import { Empty } from "../ui/Empty";

type Driver = { spent: number; limit: number; unit: string; pct: number; warn: boolean; exhausted: boolean };
type Kpis = { drift_pct: number; touch_drift: number; rubber_stamp_rate: number; rubber_stamps: number; approvals_timed: number; tokens_per_node: number; spend_vs_budget: number; spend_by_driver: Record<string, Driver> };

const pct = (x: number) => (x * 100).toFixed(1) + "%";

export function SpendPage() {
  const [k, setK] = useState<Kpis | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<Kpis>(routes.kpis()).then(
      (d) => { if (!alive) return; setK(d); setError(null); },
      (e) => { if (!alive) return; setError(e.message); },
    );
    return () => { alive = false; };
  }, [refreshTick.value]);
  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!k) return <div class="page"><p class="muted">loading…</p></div>;
  const cards: [string, string, string?][] = [
    ["Components verified in the last 50 commits", pct(k.drift_pct)],
    ["Touches outside the prediction", pct(k.touch_drift)],
    ["Rubber-stamp rate (medium/high)", pct(k.rubber_stamp_rate), `${k.rubber_stamps} fast of ${k.approvals_timed} timed approvals`],
    ["Tokens per task", String(Math.round(k.tokens_per_node))],
    ["Worst agent spend vs budget", pct(k.spend_vs_budget)],
  ];
  const drivers = Object.entries(k.spend_by_driver || {});
  return (
    <div class="page stack">
      <h1 class="page-title">Spend</h1>
      <div class="grid-kpi">
        {cards.map(([label, value, sub]) => <div class="card kpi"><div class="kpi-value">{value}</div><div class="caption">{label}</div>{sub ? <div class="caption">{sub}</div> : null}</div>)}
      </div>
      <h2>Spend per agent</h2>
      {drivers.length ? drivers.map(([name, d]) => (
        <div class="card stack tight">
          <div class="row between"><strong>{name}</strong><span class="caption">{d.spent} / {d.limit} {d.unit}</span></div>
          <div class={"progress" + (d.exhausted ? " exhausted" : d.warn ? " warn" : "")}><span style={{ width: Math.min(100, d.pct * 100) + "%" }} /></div>
          <div class="caption">{pct(d.pct)}{d.exhausted ? " · exhausted" : d.warn ? " · warning" : ""}</div>
        </div>
      )) : <Empty text="No agent has a [agents.<name>.budget]." />}
    </div>
  );
}
