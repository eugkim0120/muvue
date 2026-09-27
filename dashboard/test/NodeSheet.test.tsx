import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { NodeSheet } from "../src/node/NodeSheet";
import { authed } from "../src/state";
import { resetRouteFromLocation } from "../src/router";

function detail(status: string, kind = "task") {
  return { node: { id: 7, project_id: 1, parent_id: 1, kind, title: "Do it", status, risk_tier: "low", owner: null, body_md: "body", criteria_json: '["a","b"]', criteria_mode: "auto", summary: null, block_reason: null }, notes: [], commits: [], predicted_touches: [], verification: "checked_by_muvue" };
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

test("a pending spec shows Approve spec above the segments", async () => {
  stubApi("pending", "spec");
  render(<NodeSheet />);
  const btn = await screen.findByText("Approve spec");
  const overview = screen.getByText("Overview");
  expect(btn.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("review shows Approve and Reject; Reject posts the feedback", async () => {
  const calls = stubApi("review");
  render(<NodeSheet />);
  await screen.findByText("Approve");
  fireEvent.click(screen.getByText("Reject"));
  const ta = screen.getByPlaceholderText("what should change?");
  fireEvent.input(ta, { target: { value: "more tests" } });
  fireEvent.click(screen.getByText("Send"));
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
  await screen.findByText("Overview");
  expect(screen.queryByText("Approve")).toBeNull();
});
