export function StatusLine({ phase, done, total, busyAgents, spend, budget }: { phase: string; done: number; total: number; busyAgents: number; spend: number; budget: number }) {
  const pct = budget > 0 ? spend / budget : 0;
  const spendClass = pct >= 1 ? "spend-red" : pct >= 0.8 ? "spend-amber" : "";
  return (
    <div class="status-line caption">
      {phase} · {done}/{total} done · {busyAgents} agents busy · <span class={spendClass}>${spend.toFixed(2)} of ${budget.toFixed(2)}</span>
    </div>
  );
}
