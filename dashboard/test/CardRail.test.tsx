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

test("the collapse control is labelled 'Hide' and collapses to a count pill", () => {
  render(<CardRail cards={cards} authed />);
  fireEvent.click(screen.getByLabelText("hide needs-you list"));
  expect(screen.getByText("2 things need you")).toBeTruthy();
});

function phone(matches: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches, media: q, addEventListener() {}, removeEventListener() {} }));
}
afterEach(() => vi.unstubAllGlobals());

test("on a phone the rail starts as a one-line chip and expands on tap", () => {
  phone(true);
  render(<CardRail cards={cards} authed />);
  expect(screen.queryByText("Approve")).toBeNull();
  fireEvent.click(screen.getByText("2 things need you"));
  expect(screen.getAllByText("Approve")).toHaveLength(2);
});

test("on a desktop the rail starts expanded", () => {
  phone(false);
  render(<CardRail cards={cards} authed />);
  expect(screen.getAllByText("Approve")).toHaveLength(2);
});

test("the chip reads right for one item and for several", () => {
  phone(true);
  const { unmount } = render(<CardRail cards={cards.slice(0, 1)} authed />);
  expect(screen.getByText("1 thing needs you")).toBeTruthy();
  unmount();
  render(<CardRail cards={cards} authed />);
  expect(screen.getByText("2 things need you")).toBeTruthy();
});

test("expanding moves focus to the rail heading and Hide returns it to the chip", () => {
  phone(true);
  render(<CardRail cards={cards} authed />);
  const chip = screen.getByText("2 things need you");
  expect(chip.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(chip);
  const heading = screen.getByText(/Needs you · 2/);
  expect(document.activeElement).toBe(heading);
  const hide = screen.getByLabelText("hide needs-you list");
  expect(hide.getAttribute("aria-expanded")).toBe("true");
  expect(hide.getAttribute("aria-controls")).toBe(chip.getAttribute("aria-controls"));
  expect(document.getElementById(hide.getAttribute("aria-controls")!)).toBeTruthy();
  fireEvent.click(hide);
  expect(document.activeElement).toBe(screen.getByText("2 things need you"));
});

test("nothing steals focus on first render", () => {
  phone(false);
  render(<CardRail cards={cards} authed />);
  expect(document.activeElement).toBe(document.body);
});
