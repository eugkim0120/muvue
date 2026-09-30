import { render, screen, fireEvent } from "@testing-library/preact";
import { CardRail } from "../src/cards/CardRail";
import { signInOpen } from "../src/state";
import type { Card } from "../src/cards/cardsFromInbox";

const cards: Card[] = [
  { id: "task_review:5", kind: "task_review", nodeId: 5, projectId: 1, title: "Detect pitch", context: "in review", agent: "claude", ts: "" },
  { id: "task_review:6", kind: "task_review", nodeId: 6, projectId: 1, title: "Split voices", context: "in review", agent: "claude", ts: "" },
];

test("signed out: no write buttons, exactly one sign-in affordance", () => {
  signInOpen.value = false;
  render(<CardRail cards={cards} authed={false} />);
  expect(screen.queryByText("Approve")).toBeNull();
  const signIn = screen.getAllByText("Sign in to act");
  expect(signIn).toHaveLength(1);
  fireEvent.click(signIn[0]!);
  expect(signInOpen.value).toBe(true);
});

test("signed in: each card keeps its Approve button and there is no sign-in prompt", () => {
  render(<CardRail cards={cards} authed />);
  expect(screen.getAllByText("Approve")).toHaveLength(2);
  expect(screen.queryByText("Sign in to act")).toBeNull();
});
