import { useRef, useState } from "preact/hooks";
import { toastError } from "../state";

// One in-flight guard + visible error per action button. `run` refuses to
// start a second call while one is pending, so a double tap cannot launch
// two breakdowns or two runs.
export function useAction(): { busy: boolean; error: string | null; run: (fn: () => Promise<unknown>) => Promise<boolean>; clearError: () => void } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      toastError(e);
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return { busy, error, run, clearError: () => setError(null) };
}
