import { render, screen, waitFor } from "@testing-library/preact";
import { ProjectPage } from "../src/project/ProjectPage";
import * as client from "../src/api/client";
import { authed, projectId, projects } from "../src/state";

test("renders Canvas on desktop width using merged /graph and /nodes data", async () => {
  projects.value = [{ id: 1, goal: "Voxscore", phase: "planning" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, agent: null }], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null }]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Voxscore"));
});

test("shows fake-agent notice when a /graph node has agent === 'fake'", async () => {
  projects.value = [{ id: 1, goal: "Voxscore", phase: "planning" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, agent: "fake" }], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null }]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => {
    const notice = document.querySelector("[data-fake-notice]");
    expect(notice).toBeTruthy();
    expect(notice?.textContent).toContain("demo agent");
  });
});

test("Important #4: at plan_tasks, only NextStepBar's plan-tasks action shows -- no duplicate header + Task button", async () => {
  authed.value = true;
  projects.value = [{ id: 1, goal: "Voxscore", phase: "planning" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "ready", risk_tier: "low", owner: null, agent: null }], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([{ id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "ready", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null }]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => screen.getAllByText("✨ Plan tasks with agent"));
  expect(screen.getAllByText("✨ Plan tasks with agent")).toHaveLength(1);
  expect(screen.queryByText("+ Task")).toBeNull();
  authed.value = false;
});

test("Minor: SubmitSpecForm is gated on authed, matching NextStepBar's read-only messaging", async () => {
  authed.value = false;
  projects.value = [{ id: 1, goal: "Voxscore", phase: "planning" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Read-only. Open the link printed by muvue serve to act."));
  expect(screen.queryByPlaceholderText("title")).toBeNull();
});
