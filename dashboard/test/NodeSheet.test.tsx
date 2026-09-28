import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { NodeSheet } from "../src/node/NodeSheet";
import { App } from "../src/app";
import { authed } from "../src/state";
import { resetRouteFromLocation } from "../src/router";

function detail(status: string, kind = "task", extra: { criteria_hash?: string | null; deleted_at?: string | null } = {}) {
  return { node: { id: 7, project_id: 1, parent_id: 1, kind, title: "Do it", status, risk_tier: "low", owner: null, body_md: "body", criteria_json: '["a","b"]', criteria_mode: "auto", summary: null, block_reason: null, criteria_hash: extra.criteria_hash ?? null, deleted_at: extra.deleted_at ?? null }, notes: [], commits: [], predicted_touches: [], verification: "checked_by_muvue" };
}

function stubApi(status: string, kind = "task") {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = url.startsWith("/nodes/7/diff") ? { source: "none", diff: "", truncated: false }
      : url === "/agents" ? { agents: ["claude", "fake"] } : url.startsWith("/nodes/7/approve") || url.startsWith("/nodes/7/reject") ? { ok: 1 } : detail(status, kind);
    const isText = url.startsWith("/nodes/7/logs");
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": isText ? "text/plain" : "application/json" }), json: async () => json, text: async () => "log line" };
  }));
  return calls;
}

beforeEach(() => { authed.value = true; window.location.hash = "#/plan?node=7"; resetRouteFromLocation(); });
afterEach(() => vi.unstubAllGlobals());

test("a pending spec shows Approve spec above the spec body", async () => {
  stubApi("pending", "spec");
  render(<NodeSheet />);
  const btn = await screen.findByText("Approve spec");
  const specBody = screen.getByText("Tap a line to comment on it. The agent sees comments in its brief.");
  expect(btn.compareDocumentPosition(specBody) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("review shows Approve and Reject; Reject posts the feedback", async () => {
  const calls = stubApi("review");
  render(<NodeSheet />);
  await screen.findByText("Approve");
  fireEvent.click(screen.getByText("Reject"));
  const ta = screen.getByPlaceholderText("what should change?");
  fireEvent.input(ta, { target: { value: "more tests" } });
  // Discussion's own reply box also has a "Send" button; the reject flow's is the first in DOM order.
  fireEvent.click(screen.getAllByText("Send")[0]!);
  await waitFor(() => expect(calls.some((c) => c.url === "/nodes/7/reject" && c.init?.body === '{"feedback":"more tests"}')).toBe(true));
});

test("ready shows Start with agent, and the picker lists agents", async () => {
  stubApi("ready");
  render(<NodeSheet />);
  fireEvent.click(await screen.findByText("Start with agent"));
  expect(await screen.findByText("claude")).toBeInTheDocument();
});

test("read-only shows no actions", async () => {
  authed.value = false;
  stubApi("review");
  render(<NodeSheet />);
  await screen.findByText("Runs");
  expect(screen.queryByText("Approve")).toBeNull();
});

test("switching to a different node id remounts the sheet, so a half-typed rejection doesn't leak across nodes", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const idMatch = /^\/nodes\/(\d+)/.exec(url);
    const id = idMatch ? Number(idMatch[1]) : 7;
    // ProjectPage now always mounts underneath NodeSheet, so its own
    // /graph and /nodes(list) fetches need shapes buildCanvasData won't choke on.
    const json = url.includes("/diff") ? { source: "none", diff: "", truncated: false }
      : url.startsWith("/agents") ? { agents: [] }
      : url.startsWith("/graph") ? { nodes: [], edges: [] }
      : url === "/nodes" || url.startsWith("/nodes?") ? []
      : url === "/inbox" ? { questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] }
      : url.includes("/revisions") ? []
      : { node: { id, project_id: 1, parent_id: 1, kind: "task", title: `Task ${id}`, status: "review", risk_tier: "low", owner: null, body_md: "body", criteria_json: "[]", criteria_mode: "auto", summary: null, block_reason: null }, notes: [], commits: [], predicted_touches: [], verification: "checked_by_muvue" };
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => json, text: async () => "" };
  }));
  window.location.hash = "#/plan?node=7";
  resetRouteFromLocation();
  render(<App />);
  fireEvent.click(await screen.findByText("Reject"));
  fireEvent.input(screen.getByPlaceholderText("what should change?"), { target: { value: "half-typed feedback for #7" } });
  expect(screen.getByPlaceholderText("what should change?")).toHaveValue("half-typed feedback for #7");

  window.location.hash = "#/plan?node=8";
  resetRouteFromLocation();
  await screen.findByText(/Task 8/);
  expect(screen.queryByPlaceholderText("what should change?")).toBeNull();
  fireEvent.click(await screen.findByText("Reject"));
  expect(screen.getByPlaceholderText("what should change?")).toHaveValue("");
});

test("a task before Gate 2 shows a Remove action", async () => {
  stubApi("ready");
  render(<NodeSheet />);
  await screen.findByText("Start with agent");
  expect(screen.getByText("Remove")).toBeTruthy();
});

test("a spec node's sheet shows SpecBody instead of the purpose/criteria section", async () => {
  stubApi("pending", "spec");
  const { container } = render(<NodeSheet />);
  await screen.findByText("Approve spec");
  expect(screen.queryByText("Overview")).toBeNull();
  expect(container.querySelectorAll(".spec-line").length).toBeGreaterThan(0);
});
