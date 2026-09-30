import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { Card } from "../src/cards/Card";
import * as client from "../src/api/client";

const base = { projectId: 1, title: "Detect pitch", context: "in review", agent: "claude", ts: "", raw: {} } as const;

test("approve calls the approve route for the card's kind and shows a spinner while pending", async () => {
  const spy = vi.spyOn(client, "post").mockResolvedValue({});
  render(<Card card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  fireEvent.click(screen.getByText("Approve"));
  await waitFor(() => expect(spy).toHaveBeenCalledWith("/nodes/5/approve", { target: "review" }));
});

test("reject disables Send until a reason is typed", () => {
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
  render(<Card readOnly card={{ id: "task_review:5", kind: "task_review", nodeId: 5, ...base }} onActed={() => {}} />);
  expect(screen.queryByText("Approve")).toBeNull();
  expect(screen.queryByText("Reject")).toBeNull();
  expect(screen.queryByText("Discuss")).toBeNull();
  expect(screen.getByText("Detect pitch")).toBeEnabled();
});
