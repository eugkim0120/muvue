import { render, screen, fireEvent } from "@testing-library/preact";
import { InboxPage, countInbox } from "../src/inbox/InboxPage";
import { authed, inboxCount, refreshTick } from "../src/state";

const empty = { questions: [], review: [], unverified_external: [], awaiting_approval: [], blocked: [], structure_updates: [], audit_items: [], signals: [], unattributed_commits: [] };

function stub(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => data, text: async () => "" })));
}
beforeEach(() => { authed.value = true; });
afterEach(() => vi.unstubAllGlobals());

test("an empty inbox says so", async () => {
  stub(empty);
  render(<InboxPage />);
  expect(await screen.findByText("Nothing waiting on you.")).toBeInTheDocument();
});

test("the count is the sum of every list and the badge follows", async () => {
  const data = { ...empty, questions: [{ id: 1, node_id: 2, text: "which db?", default_answer: "sqlite", default_ok: true }], review: [{ id: 2, project_id: 1, parent_id: 1, kind: "task", title: "x", status: "review" as const, risk_tier: "high" as const, owner: null }], signals: [{ id: 9, ts: "t", node_id: null, payload: '{"sha":"abcdef123456","files":["a.py"]}' }] };
  expect(countInbox(data)).toBe(3);
  stub(data);
  render(<InboxPage />);
  expect(await screen.findByText("which db?")).toBeInTheDocument();
  expect(inboxCount.value).toBe(3);
});

test("unverified_external alone is counted, not undercounted to zero", () => {
  const data = { ...empty, unverified_external: [{ node_id: 1, title: "a" }, { node_id: 2, title: "b" }] };
  expect(countInbox(data)).toBe(2);
});

test("typed-but-unsent answer for a question does not leak onto a different question rendered in its slot", async () => {
  const q1 = { id: 1, node_id: 2, text: "which db?", default_answer: "sqlite" as string | null, default_ok: true };
  const q2 = { id: 2, node_id: 3, text: "which host?", default_answer: null as string | null, default_ok: false };
  let data = { ...empty, questions: [q1] };
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => data, text: async () => "" })));
  render(<InboxPage />);
  await screen.findByText("which db?");
  fireEvent.input(screen.getByPlaceholderText("answer"), { target: { value: "half-typed answer" } });
  expect(screen.getByPlaceholderText("answer")).toHaveValue("half-typed answer");

  // q1 is answered/dismissed and a different question now occupies the same slot 0.
  data = { ...empty, questions: [q2] };
  refreshTick.value++;
  await screen.findByText("which host?");
  expect(screen.queryByText("which db?")).toBeNull();
  const remaining = screen.getByPlaceholderText("answer") as HTMLInputElement;
  expect(remaining.value).toBe("");
});

test("a javascript: pr_url never becomes an href, but still shows as text", async () => {
  const data = { ...empty, structure_updates: [{ id: 5, ts: "t", node_id: null, payload: '{"ref":"main","sha":"abcdef123456","pr_url":"javascript:alert(1)"}' }] };
  stub(data);
  render(<InboxPage />);
  await screen.findByText(/structure diff on main/);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
});
