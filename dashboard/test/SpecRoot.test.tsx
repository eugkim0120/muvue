import { render, screen } from "@testing-library/preact";
import { SpecRoot, SubmitSpecForm } from "../src/canvas/SpecRoot";
import { authed } from "../src/state";

beforeEach(() => {
  authed.value = true;
});

test("no spec yet shows the inline title/body submit form", () => {
  render(<SubmitSpecForm projectId={1} />);
  expect(screen.getByPlaceholderText("title")).toBeTruthy();
  expect(screen.getByText("Submit spec")).toBeTruthy();
});

test("spec pending shows the spec title, with no approve button", () => {
  render(<SpecRoot spec={{ id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "pending", agent: null }} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.queryByText("Approve task list")).toBeNull();
});

test("the spec's badge says approved or needs approval, never a node status like Ready", () => {
  const base = { id: 1, title: "voxscore v0.1", body_md: "Record voices", agent: null };
  const { container, rerender } = render(<SpecRoot spec={{ ...base, status: "pending" } as any} />);
  expect(container.querySelector(".pill")!.textContent).toBe("needs approval");
  rerender(<SpecRoot spec={{ ...base, status: "ready" } as any} />);
  expect(container.querySelector(".pill")!.textContent).toBe("approved");
});
