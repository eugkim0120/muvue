import { render, screen } from "@testing-library/preact";
import { SpecCard } from "../src/plan/SpecCard";
import { authed, projects, projectId } from "../src/state";

const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending" as const, risk_tier: "low" as const, owner: null, body_md: "line one\nline two" };

beforeEach(() => { authed.value = true; projects.value = [{ id: 1, goal: "g", phase: "planning" }]; projectId.value = 1; });

test("a pending spec offers Approve spec", () => {
  render(<SpecCard spec={spec} taskCount={0} />);
  expect(screen.getByText("Approve spec")).toBeInTheDocument();
  expect(screen.queryByText("Approve task list")).toBeNull();
});

test("an approved spec with tasks in planning offers Approve task list", () => {
  render(<SpecCard spec={{ ...spec, status: "ready" }} taskCount={3} />);
  expect(screen.getByText("Approve task list")).toBeInTheDocument();
});

test("read-only shows neither button", () => {
  authed.value = false;
  render(<SpecCard spec={spec} taskCount={3} />);
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.queryByText("Approve task list")).toBeNull();
});
