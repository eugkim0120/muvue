// The only module that spells an API path. tests/test_dashboard_static.py
// reads this file and checks every path against the daemon's routes.
function q(path: string, params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) search.set(k, String(v));
  const s = search.toString();
  return s ? `${path}?${s}` : path;
}

export const routes = {
  healthz: () => "/healthz",
  authCheck: () => "/auth/check",
  authWhoami: () => "/auth/whoami",
  authExchange: () => "/auth/exchange",
  authSession: () => "/auth/session",
  eventsStream: () => "/events/stream",
  projects: () => "/projects",
  projectSpec: (id: number) => `/projects/${id}/spec`,
  agents: () => "/agents",
  inbox: () => "/inbox",
  kpis: () => "/kpis",
  graph: (projectId: number | null) => q("/graph", { project_id: projectId }),
  nodes: (projectId: number | null) => q("/nodes", { project_id: projectId }),
  events: (projectId: number | null, limit = 100) => q("/events", { limit, project_id: projectId }),
  node: (id: number) => `/nodes/${id}`,
  nodeDiff: (id: number) => `/nodes/${id}/diff`,
  nodeLogs: (id: number, lines = 200) => q(`/nodes/${id}/logs`, { lines }),
  nodeApprove: (id: number) => `/nodes/${id}/approve`,
  approveRevision: (projectId: number) => `/nodes/${projectId}/approve`,
  nodeReject: (id: number) => `/nodes/${id}/reject`,
  nodeStart: (id: number, agent: string) => q(`/nodes/${id}/start`, { agent }),
  nodeComment: (id: number) => `/nodes/${id}/comment`,
  questionAnswer: (id: number) => `/questions/${id}/answer`,
  eventAck: (id: number) => `/events/${id}/ack`,
  revisions: (projectId: number) => `/projects/${projectId}/revisions`,
  projectPause: (projectId: number) => `/projects/${projectId}/pause`,
  projectResume: (projectId: number) => `/projects/${projectId}/resume`,
  closePreview: (projectId: number) => `/projects/${projectId}/close-preview`,
  projectClose: (projectId: number) => `/projects/${projectId}/close`,
  nodeChildren: (id: number) => `/nodes/${id}/children`,
  nodeRemove: (id: number) => `/nodes/${id}/remove`,
  nodeEdit: (id: number) => `/nodes/${id}/edit`,
  nodeBreakdown: (id: number) => `/nodes/${id}/breakdown`,
  projectRun: (id: number) => `/projects/${id}/run`,
  agentsStatus: (projectId: number) => q("/agents/status", { project_id: projectId }),
  nodeRuns: (id: number) => `/nodes/${id}/runs`,
  projectActivity: (id: number) => `/projects/${id}/activity`,
  projectLogs: (id: number, lines = 200) => q(`/projects/${id}/logs`, { lines }),
};
