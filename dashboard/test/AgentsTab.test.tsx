import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AgentsTab } from "../src/agents/AgentsTab";
import * as client from "../src/api/client";
import { authed } from "../src/state";
import { wt, session } from "./agentsFixtures";

const SESSIONS = [
  session({ id: "run-1", origin: "launched", node_id: 12, node_title: "Detect pitch", cwd: "/w/node-12", last_action: "Edit core/pitch.py", spend: { in_tokens: 1000, out_tokens: 200, cost: 0.12 }, touches: ["core/pitch.py"] }),
  session({ id: "s-hand", cwd: "/repo/.worktrees/try", state: "waiting", last_action: "turn ended" }),
];
const WORKTREES = {
  errors: [],
  worktrees: [
    wt({ path: "/repo", branch: "main", is_base: true, stage: null }),
    wt({ path: "/w/node-12", branch: "node-12", ahead: 2, changed_paths: ["core/pitch.py"], fork_time: 100, tip_time: 200, stage: "commits", node_id: 12, node_title: "Detect pitch", node_status: "in_progress", session_ids: ["run-1"], planned: [{ glob: "core/*.py", touched: true }, { glob: "web/*.ts", touched: false }] }),
    wt({ path: "/repo/.worktrees/try", branch: "try", ahead: 1, changed_paths: ["a.md"], fork_time: 50, tip_time: 60, stage: "commits", session_ids: ["s-hand"] }),
    wt({ path: "/w/old", branch: "old", behind: 3, fork_time: 10, tip_time: Date.now() / 1000 - 7200, stage: "merged", merged: true }),
  ],
};

beforeEach(() => {
  authed.value = true;
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/sessions")) return Promise.resolve({ sessions: SESSIONS });
    if (path === "/worktrees") return Promise.resolve(WORKTREES);
    return Promise.reject(new Error("unexpected " + path));
  });
});
afterEach(() => { vi.restoreAllMocks(); });

const branchOrder = () => [...document.querySelectorAll("[data-branch]")].map((e) => e.getAttribute("data-branch"));

test("branches show base first, then by fork point, with state, task and stage", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  expect(branchOrder()).toEqual(["main", "try", "node-12", "old"]);
  expect(screen.getByText("waiting on you")).toBeTruthy();
  expect(screen.getByText("off-plan")).toBeTruthy();
  expect(screen.getByText("#12 Detect pitch")).toBeTruthy();
  expect(screen.getAllByLabelText("stage: commits")).toHaveLength(2);
});

test("a worktree with no agent shows whether it is merged and its last commit", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("Worktrees with no agent"));
  expect(screen.getByText("merged · last commit 2h ago")).toBeTruthy();
});

test("a hand session's panel has no Stop", async () => {
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("try"));
  fireEvent.click(document.querySelector('[data-branch="try"]')!);
  expect(screen.getByText(/started by hand/)).toBeTruthy();
  expect(screen.getByText("Off-plan: no task, so no planned files.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop agent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open diff" })).toBeNull();
});

test("a launched session's panel lists planned files and stops after a confirm", async () => {
  const post = vi.spyOn(client, "post").mockResolvedValue({ session_id: "run-1", signalled: 42 });
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  fireEvent.click(document.querySelector('[data-branch="node-12"]')!);
  expect(screen.getByText("core/*.py").nextSibling?.textContent).toBe("changed");
  expect(screen.getByText("web/*.ts").nextSibling?.textContent).toBe("not yet");
  fireEvent.click(screen.getByRole("button", { name: "Open session" }));
  expect(screen.getByText("1200 tokens · $0.12")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  expect(screen.getByText("Stop this agent?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/sessions/run-1/stop"));
});

test("signed out, the tab asks for sign-in and fetches nothing", () => {
  authed.value = false;
  render(<AgentsTab projectId={1} />);
  expect(screen.getByText("Sign in to see agent sessions and worktrees.")).toBeTruthy();
  expect(client.api).not.toHaveBeenCalled();
});

test("a refused stop shows the server's detail verbatim in the panel", async () => {
  vi.spyOn(client, "post").mockRejectedValue(new client.ApiError("started by hand; stop it where it runs", 409));
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  fireEvent.click(document.querySelector('[data-branch="node-12"]')!);
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  await waitFor(() => expect(document.querySelector(".callout.danger")?.textContent).toBe("started by hand; stop it where it runs"));
});

test("a git failure shows on its own row and the rest of the page still renders", async () => {
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/sessions")) return Promise.resolve({ sessions: [] });
    return Promise.resolve({ errors: [], worktrees: [wt({ path: "/w/bad", branch: "bad", error: "git failed: fatal: bad object", tip_time: 5 }), wt({ path: "/repo", branch: "main", is_base: true, stage: null })] });
  });
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("git failed: fatal: bad object"));
  expect(branchOrder()).toEqual(["main", "bad"]);
});

test("a git failure on the base row shows on that row", async () => {
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/sessions")) return Promise.resolve({ sessions: [] });
    return Promise.resolve({ errors: [], worktrees: [wt({ path: "/repo", branch: "main", is_base: true, stage: null, error: "git failed: fatal: not a repository" })] });
  });
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("git failed: fatal: not a repository"));
  expect(document.querySelector('[data-branch="main"]')?.textContent).toContain("git failed: fatal: not a repository");
});

test("switching project drops the previous project's sessions while the new fetch is pending", async () => {
  const { rerender } = render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-12"));
  vi.spyOn(client, "api").mockImplementation(() => new Promise(() => {}));
  rerender(<AgentsTab projectId={2} />);
  expect(screen.queryByText("node-12")).toBeNull();
  expect(screen.getByText("loading…")).toBeTruthy();
});

test("a branch where another project's agent is live shows that agent, not \"no agent\"", async () => {
  // Worktree rows are repository-wide; project 1's session list does not
  // hold project 2's run, the whole repository's list does.
  const other = session({ id: "run-7", origin: "launched", node_id: 7, node_title: "Other project", cwd: "/w/node-7", state: "working" });
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/sessions?project_id=1")) return Promise.resolve({ sessions: [] });
    if (path.startsWith("/sessions")) return Promise.resolve({ sessions: [other] });
    return Promise.resolve({ errors: [], worktrees: [
      wt({ path: "/repo", branch: "main", is_base: true, stage: null }),
      wt({ path: "/w/node-7", branch: "node-7", fork_time: 100, tip_time: 200, stage: "commits", node_id: 7, node_title: "Other project", session_ids: ["run-7"] }),
    ] });
  });
  render(<AgentsTab projectId={1} />);
  await waitFor(() => screen.getByText("node-7"));
  expect(document.querySelector('[data-branch="node-7"]')?.classList.contains("idle-row")).toBe(false);
  expect(screen.getByText("working")).toBeTruthy();
  fireEvent.click(document.querySelector('[data-branch="node-7"]')!);
  expect(screen.getByRole("button", { name: "Stop agent" })).toBeTruthy();
});
