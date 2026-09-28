import { useEffect, useState } from "preact/hooks";

// Every canvas/card/node view had its own copy of this alive-guarded
// fetch-on-deps-change effect (final review flagged the duplication in
// the dashboard-redesign plan). `fetcher` is a thunk, not a path, so a
// caller can combine two endpoints (canvasData does) or add headers.
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    fetcher().then(
      (d) => { if (alive) { setData(d); setError(null); } },
      (e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}
