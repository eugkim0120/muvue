import type { Session, Worktree } from "../src/agents/agentsData";

export function wt(over: Partial<Worktree>): Worktree {
  return {
    path: "/w", branch: "b", head: "h", base: "main", is_base: false, ahead: 0, behind: 0, changed_paths: [],
    fork_time: null, tip_time: null, merged: false, error: null, session_ids: [], node_id: null, node_title: null,
    node_status: null, stage: "worktree", planned: [], ...over,
  };
}

export function session(over: Partial<Session>): Session {
  return {
    id: "s", agent: "claude", origin: "hand", node_id: null, node_title: null, node_status: null, cwd: "/w",
    worktree: "/w", branch: "b", transcript_path: null, started_at: "2026-10-10T11:00:00.000Z",
    last_event_at: "2026-10-10T11:59:00.000Z", last_action: null, ended_at: null, waiting: false,
    state: "working", spend: null, touches: [], ...over,
  };
}
