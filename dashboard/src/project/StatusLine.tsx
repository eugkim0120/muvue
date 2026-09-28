const PHASE = { planning: "Planning", executing: "Running", paused: "Paused", closed: "Closed" };

export function StatusLine({ phase, done, total, working }: { phase: string; done: number; total: number; working: number }) {
  const phaseLabel = PHASE[phase as keyof typeof PHASE] || phase;
  const workingLabel = working === 0 ? "no agent working" : working === 1 ? "1 agent working" : `${working} agents working`;
  return (
    <div class="status-line caption">
      {phaseLabel} · {done} of {total} tasks done · {workingLabel}
    </div>
  );
}
