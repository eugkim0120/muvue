import { render, screen, waitFor, fireEvent } from "@testing-library/preact";
import { ProjectPage } from "../src/project/ProjectPage";
import * as client from "../src/api/client";
import { authed, projectId, projects } from "../src/state";

afterEach(() => { vi.restoreAllMocks(); });

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
    expect(notice?.textContent).toContain("Demo agent");
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

test("the demo notice is a banner that can be dismissed and stays dismissed", async () => {
  localStorage.clear();
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { unmount } = render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  const notice = document.querySelector("[data-fake-notice]")!;
  expect(notice.textContent).toContain("Demo agent: writes no code");
  const details = screen.getByRole("button", { name: "Details" });
  const more = document.getElementById(details.getAttribute("aria-controls")!)!;
  expect(more.textContent).toContain("canned results");
  expect(more.hidden).toBe(true);
  expect(details.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(details);
  expect(details.getAttribute("aria-expanded")).toBe("true");
  expect(more.hidden).toBe(false);
  fireEvent.click(screen.getByLabelText("dismiss demo notice"));
  expect(document.querySelector("[data-fake-notice]")).toBeNull();
  expect(screen.queryByText(/Couldn't remember this/)).toBeNull();
  unmount();
  render(<ProjectPage />);
  await waitFor(() => screen.getByText("Voxscore"));
  expect(document.querySelector("[data-fake-notice]")).toBeNull();
  localStorage.clear();
});

test("the demo banner sits in the header between the meta row and the toolbar", async () => {
  localStorage.clear();
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { container } = render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  const head = container.querySelector(".project-head")!;
  const kids = [...head.children];
  const at = (sel: string) => kids.findIndex((k) => k.matches(sel));
  expect(at("[data-fake-notice]")).toBeGreaterThan(at(".meta-row"));
  expect(at("[data-fake-notice]")).toBeLessThan(at(".toolbar"));
});

test("the demo banner still renders and dismisses when storage throws", async () => {
  localStorage.clear();
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  fireEvent.click(screen.getByLabelText("dismiss demo notice"));
  expect(document.querySelector("[data-fake-notice]")).toBeNull();
  expect(screen.getByText(/Couldn't remember this; it will return on reload\./)).toBeTruthy();
});

test("the header shows task progress, with spend only when a budget exists", async () => {
  mockProject("ready", [{ id: 2, status: "done" }, { id: 3, status: "in_progress" }], "executing");
  const base = vi.mocked(client.api).getMockImplementation()!;
  vi.mocked(client.api).mockImplementation((path: string) => path === "/kpis" ? Promise.resolve({ spend_by_driver: { claude: { unit: "usd", spent: 3.2, limit: 10, pct: 0.32 } } }) : base(path));
  const { container, unmount } = render(<ProjectPage />);
  await waitFor(() => screen.getByRole("progressbar"));
  expect(screen.getByRole("progressbar")).toHaveAccessibleName("1 done, 1 running, 0 in review, 0 blocked, of 2 tasks");
  expect(container.querySelector(".project-head .progress-row")).toBeTruthy();
  await waitFor(() => screen.getByText("$3.20 of $10.00 (claude)"));
  unmount();
  let kpisSettled = false;
  vi.mocked(client.api).mockImplementation((path: string) => path === "/kpis" ? Promise.resolve({ spend_by_driver: {} }).then((v) => { kpisSettled = true; return v; }) : base(path));
  const r = render(<ProjectPage />);
  await waitFor(() => screen.getByRole("progressbar"));
  await waitFor(() => expect(kpisSettled).toBe(true));
  await new Promise((res) => setTimeout(res, 0));
  expect(r.container.querySelector(".progress-spend")).toBeNull();
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

test("signed out: Run tasks is disabled with a sign-in reason and no Approve button is shown", async () => {
  authed.value = false;
  projects.value = [{ id: 1, goal: "Voxscore", phase: "executing" }];
  projectId.value = 1;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "done", risk_tier: "low", owner: null, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null };
    const task = { id: 2, project_id: 1, parent_id: 1, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "a", body_md: "y", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null };
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [{ ...spec, agent: null }, { ...task, agent: "claude" }], edges: [] });
    if (path.startsWith("/nodes/2/diff")) return Promise.resolve({ node_id: 2, source: "none", diff: "", truncated: false, commits: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([spec, task]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [task], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
  render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector(".notif-card")).toBeTruthy());
  expect(screen.getAllByText("Sign in to act")).toHaveLength(1);
  expect(screen.queryByText("Approve")).toBeNull();
  expect(screen.getByText("▶ Run tasks")).toBeDisabled();
  expect(document.querySelector("[data-run-reason]")?.textContent).toMatch(/sign in/i);
});

test("a failing /kpis request is shown, not silently dropped", async () => {
  mockProject("ready", [{ id: 2, status: "done" }], "executing");
  const base = vi.mocked(client.api).getMockImplementation()!;
  vi.mocked(client.api).mockImplementation((path: string) => path === "/kpis" ? Promise.reject(new Error("kpis down")) : base(path));
  render(<ProjectPage />);
  const msg = await waitFor(() => screen.getByText(/Spend unavailable: kpis down/));
  expect(msg.classList.contains("caption")).toBe(true);
  expect(document.querySelector(".callout.danger")).toBeNull();
});
