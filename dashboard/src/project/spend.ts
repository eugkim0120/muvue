export type Kpis = { spend_by_driver: Record<string, { unit: string; spent: number; limit: number; pct: number }> };

const usd = (n: number) => `$${n.toFixed(2)}`;

// Only drivers with a configured budget appear in /kpis, so no budget means no label.
export function spendLabel(kpis: Kpis | null): string | null {
  const drivers = Object.entries(kpis?.spend_by_driver ?? {});
  if (!drivers.length) return null;
  const [name, d] = drivers.reduce((a, b) => (b[1].pct > a[1].pct ? b : a));
  return d.unit === "usd" ? `${usd(d.spent)} of ${usd(d.limit)} (${name})` : `${d.spent} of ${d.limit} ${d.unit} (${name})`;
}
