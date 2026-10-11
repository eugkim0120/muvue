import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { useApi } from "../hooks";
import { authed, refreshTick } from "../state";
import { Empty } from "../ui/Empty";
import {
  STAGES, STATE_CLASS, ageLabel, aheadBehindLabel, branchRows, changesLabel, stageReached, stateLabel, taskLabel,
  type BranchRow, type Session, type Stage, type WorktreeList,
} from "./agentsData";
import { BranchPanel } from "./BranchPanel";

// Liveness is computed by the server when asked; ask again this often so
// "working" turns "quiet" without a database change to trigger a refresh.
const RELOAD_MS = 30000;

function GraphCell({ base, first, last }: { base: boolean; first: boolean; last: boolean }) {
  // One trunk for the base branch down the left; each branch leaves it on
  // its own row, so rows ordered by fork point never cross.
  return (
    <svg class="git-graph" viewBox="0 0 28 52" preserveAspectRatio="none" aria-hidden="true">
      <line class="trunk" x1="8" y1={first ? 26 : 0} x2="8" y2={last ? 26 : 52} vector-effect="non-scaling-stroke" />
      {base ? <circle class="tip base" cx="8" cy="26" r="5" /> : (
        <>
          <path class="fork" d="M8 12 Q8 26 20 26" vector-effect="non-scaling-stroke" />
          <circle class="tip" cx="20" cy="26" r="4" />
        </>
      )}
    </svg>
  );
}

function StageBar({ stage }: { stage: Stage | null }) {
  const reached = stageReached(stage);
  return (
    <span class="stage-bar" role="img" aria-label={stage ? `stage: ${stage}` : "stage unknown"}>
      {STAGES.map((st, i) => <span class={"stage-seg" + (i < reached ? " on" : "")} title={st} />)}
    </span>
  );
}

function BranchLine({ row, first, last, onOpen }: { row: BranchRow; first: boolean; last: boolean; onOpen: () => void }) {
  const w = row.worktree;
  const s = row.session;
  return (
    <button type="button" class="list-row branch-row" data-branch={w.branch ?? ""} onClick={onOpen}>
      <GraphCell base={w.is_base} first={first} last={last} />
      <span class="branch-main">
        <span class="title mono">{w.branch ?? "(detached)"}</span>
        <span class="caption branch-sub">{w.path}</span>
      </span>
      <span class="branch-agent">
        {s ? (
          <>
            <span class={"pill " + STATE_CLASS[s.state]}>{stateLabel(s.state)}</span>
            <span class="caption"> {s.agent}{s.origin === "hand" ? " · by hand" : ""}</span>
          </>
        ) : <span class="caption">no agent</span>}
      </span>
      <span class="branch-task caption">{w.is_base ? "base branch" : taskLabel(w)}</span>
      {w.is_base ? (
        w.error ? <span class="branch-changes caption">{w.error}</span> : null
      ) : (
        <>
          <span class="branch-changes caption">{w.error ?? `${changesLabel(w)} · ${aheadBehindLabel(w)}`}</span>
          <StageBar stage={w.stage} />
        </>
      )}
    </button>
  );
}

// Keyed by project so a switch starts afresh: no previous selection and no
// stale data shown while the new fetch is in flight (useApi keeps it).
export function AgentsTab({ projectId }: { projectId: number | null }) {
  return <AgentsTabBody key={projectId} />;
}

function AgentsTabBody() {
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), RELOAD_MS);
    return () => clearInterval(t);
  }, []);
  const signedIn = authed.value;
  // Worktree rows are repository-wide, so their session ids are resolved
  // against every project's sessions: a branch where another project's
  // agent is live must not read "no agent".
  const sessionsQ = useApi<{ sessions: Session[] }>(
    () => (signedIn ? api(routes.sessions(null), { background: true }) : Promise.resolve({ sessions: [] })),
    [signedIn, refreshTick.value, tick],
  );
  const worktreesQ = useApi<WorktreeList>(
    () => (signedIn ? api(routes.worktrees(), { background: true }) : Promise.resolve({ worktrees: [], errors: [] })),
    [signedIn, refreshTick.value, tick],
  );
  const rows = useMemo(
    () => (worktreesQ.data && sessionsQ.data ? branchRows(worktreesQ.data, sessionsQ.data.sessions) : null),
    [worktreesQ.data, sessionsQ.data],
  );

  if (!signedIn) return <div class="agents-tab"><p class="muted">Sign in to see agent sessions and worktrees.</p></div>;
  const error = sessionsQ.error ?? worktreesQ.error;
  if (error) return <div class="agents-tab"><div class="callout danger">{error}</div></div>;
  if (!rows || !worktreesQ.data) return <div class="agents-tab"><Empty text="loading…" /></div>;

  const now = Date.now();
  const branches = [...(rows.base ? [rows.base] : []), ...rows.active];
  const open = [...branches, ...rows.idle].find((r) => r.worktree.path === selected) ?? null;
  return (
    <div class="agents-tab">
      {worktreesQ.data.errors.map((e) => <div class="callout danger">{e}</div>)}
      <section class="stack tight">
        <h2>Branches</h2>
        <div class="list">
          {branches.map((r, i) => (
            <BranchLine row={r} first={i === 0} last={i === branches.length - 1} onOpen={() => setSelected(r.worktree.path)} />
          ))}
        </div>
      </section>
      <section class="stack tight">
        <h2>Worktrees with no agent</h2>
        {rows.idle.length === 0 ? <p class="muted">None.</p> : (
          <div class="list">
            {rows.idle.map((r) => (
              <button type="button" class="list-row branch-row idle-row" data-branch={r.worktree.branch ?? ""} onClick={() => setSelected(r.worktree.path)}>
                <span class="branch-main">
                  <span class="title mono">{r.worktree.branch ?? "(detached)"}</span>
                  <span class="caption branch-sub">{r.worktree.path}</span>
                </span>
                <span class="caption branch-agent">
                  {r.worktree.error ?? `${r.worktree.merged ? "merged" : "not merged"} · ${r.worktree.tip_time !== null ? `last commit ${ageLabel(r.worktree.tip_time, now)}` : "no commits"}`}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
      {open ? <BranchPanel row={open} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
