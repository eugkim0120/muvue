import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";
import { projects, projectId, authed } from "../src/state";

beforeEach(() => {
  projects.value = [{ id: 1, goal: "build the thing", phase: "planning" }];
  projectId.value = 1;
  authed.value = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const json = url.startsWith("/graph") ? { nodes: [], edges: [] }
      : url === "/inbox" ? { questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] }
      : url.startsWith("/projects/1/activity") ? { active: [], breakdowns: [], working: [] }
      : [];
    return { ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => json, text: async () => "" };
  }));
  vi.stubGlobal("EventSource", undefined);
});
afterEach(() => vi.unstubAllGlobals());

test("the shell shows the project and no sign-in strip when signed in", () => {
  render(<App />);
  expect(screen.getAllByText("build the thing").length).toBeGreaterThan(0);
  expect(document.querySelector("[data-auth-strip]")).toBeNull();
});

test("read-only shows the one-line sign-in strip, not a token form", () => {
  authed.value = false;
  render(<App />);
  expect(document.querySelector('[data-auth-strip="read_only"]')).toBeTruthy();
  expect(screen.queryByLabelText("api token")).toBeNull();
});
