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
  await waitFor(() => screen.getByText("Sign in to act"));
  expect(screen.queryByPlaceholderText("title")).toBeNull();
});

function mockProject(specStatus: string, tasks: Array<{ id: number; status: string }>, phase: "planning" | "executing") {
  projects.value = [{ id: 1, goal: "Voxscore", phase }];
  projectId.value = 1;
  const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: specStatus, risk_tier: "low", owner: null, agent: "fake" };
  const taskRows = tasks.map((t) => ({ id: t.id, project_id: 1, parent_id: 1, kind: "task", title: "T" + t.id, status: t.status, risk_tier: "low", owner: null, agent: "fake" }));
  const full = (n: any) => ({ ...n, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null });
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [spec, ...taskRows], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([full(spec), ...taskRows.map(full)]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
}

const runButton = () => screen.getByText("▶ Run tasks") as HTMLButtonElement;

test("one primary action: Run enabled but another step is next, so Run is not filled", async () => {
  authed.value = true;
  mockProject("ready", [{ id: 2, status: "ready" }, { id: 3, status: "in_progress" }], "executing");
  const { container } = render(<ProjectPage />);
  await waitFor(() => screen.getByText("Agents are working"));
  expect(runButton().disabled).toBe(false);
  expect(runButton().classList.contains("btn-filled")).toBe(false);
  expect(container.querySelectorAll(".btn-filled").length).toBeLessThanOrEqual(1);
  authed.value = false;
});

test("one primary action: while the task list awaits approval, only Approve is filled", async () => {
  authed.value = true;
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { container } = render(<ProjectPage />);
  await waitFor(() => screen.getByText("Approve task list"));
  expect(runButton().classList.contains("btn-filled")).toBe(false);
  expect([...container.querySelectorAll(".btn-filled")].map((b) => b.textContent)).toEqual(["Approve task list"]);
  authed.value = false;
});

test("when Run is the next step, Run is enabled and the only filled button", async () => {
  authed.value = true;
  mockProject("ready", [{ id: 2, status: "ready" }], "executing");
  const { container } = render(<ProjectPage />);
  await waitFor(() => screen.getByText("Run the tasks"));
  expect(runButton().disabled).toBe(false);
  expect([...container.querySelectorAll(".btn-filled")].map((b) => b.textContent)).toEqual(["▶ Run tasks"]);
  authed.value = false;
});

test("the fake-agent notice is one tappable line that expands for detail", async () => {
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  const notice = document.querySelector("[data-fake-notice]")!;
  expect(notice.tagName).toBe("DETAILS");
  expect(notice.querySelector("summary")!.textContent).toBe("Demo agent: writes no code");
  expect(notice.textContent).toContain("demo agent");
});

test("the header, rail and diagram are separate regions, with the Next step in the rail", async () => {
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { container } = render(<ProjectPage />);
  await waitFor(() => container.querySelector(".canvas-wrap"));
  expect(container.querySelector(".project-head h1")!.textContent).toBe("Voxscore");
  expect(container.querySelector(".project-rail [data-next-step]")).toBeTruthy();
  expect(container.querySelector(".project-dag .canvas-wrap")).toBeTruthy();
  expect(screen.queryByText("Nothing waiting on you")).toBeNull();
});
