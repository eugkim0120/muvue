export type Counts = { done: number; running: number; review: number; blocked: number; total: number };

export function progressCounts(tasks: { status: string }[]): Counts {
  const n = (...s: string[]) => tasks.filter((t) => s.includes(t.status)).length;
  return { done: n("done"), running: n("in_progress"), review: n("review", "awaiting_approval"), blocked: n("blocked", "failed"), total: tasks.length };
}

export function ProgressBar({ counts, spend }: { counts: Counts; spend: string | null }) {
  if (!counts.total) return null;
  const pct = (n: number) => (n / counts.total) * 100 + "%";
  const label = `${counts.done} done, ${counts.running} running, ${counts.review} in review, ${counts.blocked} blocked, of ${counts.total} tasks`;
  return (
    <div class="progress-row">
      <div class="progress-seg" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={counts.total} aria-valuenow={counts.done}>
        <span class="seg seg-done" style={{ width: pct(counts.done) }} />
        <span class="seg seg-running" style={{ width: pct(counts.running) }} />
        <span class="seg seg-review" style={{ width: pct(counts.review) }} />
        <span class="seg seg-blocked" style={{ width: pct(counts.blocked) }} />
      </div>
      <span class="caption">{`${counts.done} done · ${counts.running} running · ${counts.review} in review · ${counts.blocked} blocked`}</span>
      {spend ? <span class="caption progress-spend">{spend}</span> : null}
    </div>
  );
}
