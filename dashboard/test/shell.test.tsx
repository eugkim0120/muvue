import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";
import { projects, projectId, authed, inboxCount } from "../src/state";

beforeEach(() => {
  projects.value = [{ id: 1, goal: "build the thing", phase: "planning" }];
  projectId.value = 1;
  authed.value = true;
  inboxCount.value = 3;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => ({ nodes: [], edges: [] }), text: async () => "" })));
  vi.stubGlobal("EventSource", undefined);
});
afterEach(() => vi.unstubAllGlobals());

test("the shell shows the project, the inbox badge and no token banner when signed in", () => {
  render(<App />);
  expect(screen.getAllByText("build the thing").length).toBeGreaterThan(0);
  expect(screen.getAllByText("3").length).toBeGreaterThan(0);
  expect(screen.queryByPlaceholderText(/api token/)).toBeNull();
});

test("read-only shows the token banner", () => {
  authed.value = false;
  render(<App />);
  expect(screen.getByPlaceholderText(/api token/)).toBeInTheDocument();
});
