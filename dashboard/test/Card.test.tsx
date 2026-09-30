import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { Card } from "../src/cards/Card";
import * as client from "../src/api/client";

const base = { projectId: 1, title: "Detect pitch", context: "in review", agent: "claude", ts: "", raw: {} } as const;

afterEach(() => { vi.restoreAllMocks(); });

const noDiff = () => { vi.spyOn(client, "api").mockResolvedValue({ source: "none", diff: "", truncated: false, commits: [] }); };

test("approve calls the approve route for the card's kind and shows a spinner while pending", async () => {
  noDiff();
  const spy = vi.spyOn(client, "post").mockResolvedValue({});
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Approve"));
  await waitFor(() => expect(spy).toHaveBeenCalledWith("/nodes/5/approve", { target: "review" }));
});

test("reject disables Send until a reason is typed", () => {
  noDiff();
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Reject"));
  expect(screen.getByText("Send")).toBeDisabled();
  fireEvent.input(screen.getByPlaceholderText("why?"), { target: { value: "needs more tests" } });
  expect(screen.getByText("Send")).not.toBeDisabled();
});

test("a question card's Discuss field doubles as the answer box", async () => {
  const spy = vi.spyOn(client, "post").mockResolvedValue({});
  render(<Card card={{ id: "question:9", kind: "question", nodeId: 5, ...base, context: "which encoder?" }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Answer"));
  fireEvent.input(screen.getByPlaceholderText("reply"), { target: { value: "opus" } });
  fireEvent.click(screen.getByText("Send"));
  await waitFor(() => expect(spy).toHaveBeenCalled());
});

test("a read-only card shows no Approve, Reject or Discuss, but the title still opens the node", () => {
  noDiff();
  render(<Card readOnly card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  expect(screen.queryByText("Approve")).toBeNull();
  expect(screen.queryByText("Reject")).toBeNull();
  expect(screen.queryByText("Discuss")).toBeNull();
  expect(screen.getByText("Detect pitch")).toBeEnabled();
});

test("a review card shows +N \u2212M, the tier and an Open task link", async () => {
  vi.spyOn(client, "api").mockResolvedValue({ node_id: 5, source: "worktree", diff: "+a\n+b\n-c\n", truncated: false, commits: [] });
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base, context: "Added a tracker.", tier: "medium", ownerLabel: "claude" }} onActed={() => {}} />);
  await waitFor(() => screen.getByText("+2 \u22121"));
  expect(screen.getByText(/medium risk/)).toBeTruthy();
  expect(screen.getByText("Open task")).toBeTruthy();
  expect(screen.getByLabelText("owner")).toHaveTextContent("claude");
});

test("a review card says when the diff could not be loaded", async () => {
  vi.spyOn(client, "api").mockRejectedValue(new Error("boom"));
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  await waitFor(() => screen.getByText("diff unavailable: boom"));
});
