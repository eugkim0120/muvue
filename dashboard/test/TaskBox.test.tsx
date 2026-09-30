import { render, screen, fireEvent } from "@testing-library/preact";
import { TaskBox } from "../src/canvas/TaskBox";
import type { CanvasTask } from "../src/canvas/canvasData";
import { authed } from "../src/state";

const task: CanvasTask = {
  id: 5, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude",
  body_md: "turn audio into notes\nmore detail", criteria_hash: null, block_reason: null,
  subtasks: [
    { id: 51, title: "YIN tracker", status: "done", parent_id: 5 },
    { id: 52, title: "smoothing", status: "ready", parent_id: 5 },
  ],
};

test("shows title, purpose line, subtask rows, and the agent chip", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Detect pitch")).toBeTruthy();
  expect(screen.getByText("turn audio into notes")).toBeTruthy();
  expect(screen.getByText("YIN tracker")).toBeTruthy();
  expect(screen.getByText(/claude/)).toBeTruthy();
});

test("more than 3 subtasks show a +N more row instead of all of them", () => {
  const many: CanvasTask = { ...task, subtasks: Array.from({ length: 6 }, (_, i) => ({ id: 60 + i, title: "s" + i, status: "ready" as const, parent_id: 5 })) };
  render(<TaskBox task={many} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("+3 more")).toBeTruthy();
  expect(screen.queryByText("s3")).toBeNull();
});

test("a needs-you badge appears when the task id is in the needsYou set, and opens the node on click", () => {
  const { container } = render(<TaskBox task={task} needsYou={new Set([5])} projectPhase="executing" />);
  expect(container.querySelector(".needs-you-dot")).toBeTruthy();
  fireEvent.click(screen.getByText("Detect pitch"));
  expect(window.location.hash).toContain("node=5");
});

test("an authed viewer sees no inline + Subtask or breakdown buttons on the box", () => {
  authed.value = true;
  const { container } = render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.queryByText("+ Subtask")).toBeNull();
  expect(screen.queryByText("✨")).toBeNull();
  expect(container.querySelector(".task-box.running")).toBeTruthy();
  authed.value = false;
});

test("the needs-you dot explains itself", () => {
  const { container } = render(<TaskBox task={task} needsYou={new Set([5])} projectPhase="executing" />);
  expect(container.querySelector(".needs-you-dot")).toHaveAttribute("title", "Needs your attention");
});
