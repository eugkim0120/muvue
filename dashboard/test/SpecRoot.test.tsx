import { render, screen, fireEvent } from "@testing-library/preact";
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

test("spec pending shows the spec title and an Approve spec button", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "pending", agent: null }} projectId={1} taskCount={0} projectPhase="planning" />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("Approve spec")).toBeTruthy();
});

test("spec approved with tasks and phase planning shows Approve task list", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "x", status: "ready", agent: null }} projectId={1} taskCount={3} projectPhase="planning" />);
  expect(screen.getByText("Approve task list")).toBeTruthy();
});
