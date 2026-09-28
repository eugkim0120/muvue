import { render, screen } from "@testing-library/preact";
import { SpecRoot } from "../src/canvas/SpecRoot";
import { authed } from "../src/state";

beforeEach(() => {
  authed.value = true;
});

test("no spec yet shows the inline title/body submit form", () => {
  render(<SpecRoot spec={null} projectId={1} taskCount={0} projectPhase="planning" />);
  expect(screen.getByPlaceholderText("title")).toBeTruthy();
  expect(screen.getByText("Submit spec")).toBeTruthy();
});

test("spec pending shows the spec title, with no approve button", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "pending", agent: null }} projectId={1} taskCount={0} projectPhase="planning" />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.queryByText("Approve task list")).toBeNull();
});
