// Shapes of GET /sessions and GET /worktrees, and the pure functions the
// Agents tab draws from them.

export type SessionState = "working" | "waiting" | "quiet" | "idle" | "ended";
export type Spend = { in_tokens: number; out_tokens: number; cost: number };
export type Session = {
  id: string; agent: string; origin: "launched" | "hand";
  node_id: number | null; node_title: string | null; node_status: string | null;
  cwd: string; worktree: string | null; branch: string | null; transcript_path: string | null;
  started_at: string; last_event_at: string; last_action: string | null; ended_at: string | null;
  waiting: boolean; state: SessionState; spend: Spend | null; touches: string[];
};
export type Stage = "worktree" | "commits" | "checks" | "review" | "merged";
export const STAGES: Stage[] = ["worktree", "commits", "checks", "review", "merged"];
export type Worktree = {
  path: string; branch: string | null; head: string | null; base: string | null; is_base: boolean;
  ahead: number | null; behind: number | null; changed_paths: string[];
  fork_time: number | null; tip_time: number | null; merged: boolean; error: string | null;
  session_ids: string[]; node_id: number | null; node_title: string | null; node_status: string | null;
  stage: Stage | null; planned: { glob: string; touched: boolean }[];
};
export type WorktreeList = { worktrees: Worktree[]; errors: string[] };
export type BranchRow = { worktree: Worktree; session: Session | null };

export function isLive(s: Session): boolean { return s.state !== "ended"; }

// Reuses the status pill colours, which the contrast test already covers.
export const STATE_CLASS: Record<SessionState, string> = {
  working: "st-in_progress", waiting: "st-awaiting_approval", quiet: "st-ready", idle: "", ended: "",
};

export function stateLabel(s: SessionState): string { return s === "waiting" ? "waiting on you" : s; }

function liveSession(ids: string[], byId: Map<string, Session>): Session | null {
  const live = ids.map((id) => byId.get(id)).filter((s): s is Session => s !== undefined && isLive(s));
  live.sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
  return live[0] ?? null;
}

// The base branch first; then each branch with a live session, ordered by
// fork point (oldest first) so the lines drawn off the base never cross;
// the rest are "Worktrees with no agent", newest commit first.
export function branchRows(list: WorktreeList, sessions: Session[]): { base: BranchRow | null; active: BranchRow[]; idle: BranchRow[] } {
  const byId = new Map(sessions.map((s) => [s.id, s] as const));
  const rows = list.worktrees.map((w) => ({ worktree: w, session: liveSession(w.session_ids, byId) }));
  const others = rows.filter((r) => !r.worktree.is_base);
  const forkOf = (r: BranchRow) => r.worktree.fork_time ?? Number.MAX_SAFE_INTEGER;
  return {
    base: rows.find((r) => r.worktree.is_base) ?? null,
    active: others.filter((r) => r.session !== null).sort((a, b) => forkOf(a) - forkOf(b) || a.worktree.path.localeCompare(b.worktree.path)),
    idle: others.filter((r) => r.session === null).sort((a, b) => (b.worktree.tip_time ?? 0) - (a.worktree.tip_time ?? 0)),
  };
}

export function stageReached(stage: Stage | null): number { return stage === null ? 0 : STAGES.indexOf(stage) + 1; }

export function taskLabel(w: Worktree): string {
  return w.node_id !== null ? `#${w.node_id} ${w.node_title ?? ""}`.trim() : "off-plan";
}

export function changesLabel(w: Worktree): string {
  const n = w.changed_paths.length;
  return n === 1 ? "1 file" : `${n} files`;
}

export function aheadBehindLabel(w: Worktree): string {
  return w.ahead === null || w.behind === null ? "–" : `↑${w.ahead} ↓${w.behind}`;
}

export function spendLabel(s: Spend): string {
  return `${s.in_tokens + s.out_tokens} tokens · $${s.cost.toFixed(2)}`;
}

// `at` is an ISO timestamp (sessions) or unix seconds (commit times).
export function ageLabel(at: string | number, now: number): string {
  const ms = typeof at === "number" ? at * 1000 : Date.parse(at);
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
