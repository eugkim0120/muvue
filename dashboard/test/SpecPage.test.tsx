import { render, screen, fireEvent, waitFor, within } from "@testing-library/preact";
import { SpecPage } from "../src/spec/SpecPage";
import { authed } from "../src/state";
import { resetRouteFromLocation } from "../src/router";

beforeEach(() => {
  authed.value = true;
  window.location.hash = "#/spec/3";
  resetRouteFromLocation();
});
afterEach(() => vi.unstubAllGlobals());

function stub() {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const body = url === "/nodes/3/comment" ? { ok: 1 } : {
      node: { id: 3, project_id: 1, parent_id: null, kind: "spec", title: "Spec", status: "ready", risk_tier: "low", owner: null, body_md: "first\nsecond", criteria_json: "[]", criteria_mode: "manual", summary: null, block_reason: null },
      notes: [{ kind: "feedback", pinned: 0, created_at: "t", text: "[L2] tighten this" }, { kind: "feedback", pinned: 0, created_at: "t", text: "overall fine" }],
      commits: [], predicted_touches: [], verification: "human",
    };
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => "" };
  }));
  return calls;
}

test("line comments render under their line and general ones at the bottom", async () => {
  stub();
  render(<SpecPage />);
  expect(await screen.findByText("tighten this")).toBeInTheDocument();
  expect(screen.getByText("overall fine")).toBeInTheDocument();
});

test("tapping a line opens a composer that posts with the line number", async () => {
  const calls = stub();
  render(<SpecPage />);
  fireEvent.click(await screen.findByText("first"));
  const input = screen.getByPlaceholderText("comment on line 1");
  fireEvent.input(input, { target: { value: "why?" } });
  fireEvent.click(within(input.closest("form")!).getByText("Comment"));
  await waitFor(() => expect(calls.some((c) => c.url === "/nodes/3/comment" && c.body === '{"text":"why?","line":1}')).toBe(true));
});
