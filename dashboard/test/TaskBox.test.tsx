import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, fireEvent } from "@testing-library/preact";
import { TaskBox } from "../src/canvas/TaskBox";
import type { CanvasTask } from "../src/canvas/canvasData";
import { authed } from "../src/state";

const task: CanvasTask = {
  id: 5, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude",
  body_md: "turn audio into notes\nmore detail", criteria_hash: null, block_reason: null,
  subtasks: [
    { id: 51, title: "YIN tracker", status: "done", parent_id: 5, owner: null },
    { id: 52, title: "smoothing", status: "ready", parent_id: 5, owner: "claude" },
  ],
};

test("shows title, purpose line, subtask rows, and the agent chip", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Detect pitch")).toBeTruthy();
  expect(screen.getByText("turn audio into notes")).toBeTruthy();
  expect(screen.getByText("YIN tracker")).toBeTruthy();
  expect(screen.getByText(/^claude · /)).toBeTruthy();
});

test("more than 3 subtasks show a +N more row instead of all of them", () => {
  const many: CanvasTask = { ...task, subtasks: Array.from({ length: 6 }, (_, i) => ({ id: 60 + i, title: "s" + i, status: "ready" as const, parent_id: 5, owner: null })) };
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

test("the parent shows 'n of m done' and each subtask shows a readable status", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("1 of 2 subtasks done")).toBeTruthy();
  expect(screen.getByLabelText("done: YIN tracker")).toBeTruthy();
  expect(screen.getByLabelText("ready: smoothing")).toBeTruthy();
});

test("a blocked task shows its reason", () => {
  render(<TaskBox task={{ ...task, status: "blocked", block_reason: "rate_limit" }} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Blocked: rate_limit")).toBeTruthy();
});

test("a blocked task with no reason still says Blocked in text, so it is not colour-only", () => {
  render(<TaskBox task={{ ...task, status: "blocked", block_reason: null }} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.getByText("Blocked")).toBeTruthy();
});

test("a task that is not blocked shows no Blocked label", () => {
  render(<TaskBox task={task} needsYou={new Set()} projectPhase="executing" />);
  expect(screen.queryByText(/^Blocked/)).toBeNull();
});

test("blocked boxes also carry a dashed left border, a non-colour shape cue", () => {
  const css = readFileSync(join(__dirname, "../src/canvas/canvas.css"), "utf8");
  expect(css).toMatch(/\.task-box\.st-bar-blocked[^{]*\{[^}]*border-left-style:\s*dashed/);
});

test("a long block reason is clamped to one line so the 18px height estimate holds", () => {
  const { container } = render(<TaskBox task={{ ...task, status: "blocked", block_reason: "rate_limit ".repeat(20) }} needsYou={new Set()} projectPhase="executing" />);
  expect(container.querySelector(".reason")!.classList.contains("clamp-1")).toBe(true);
});

test("a task whose body has no purpose line renders no empty purpose row", () => {
  const { container } = render(<TaskBox task={{ ...task, body_md: "   \n\n" }} needsYou={new Set()} projectPhase="executing" />);
  expect(container.querySelector(".purpose")).toBeNull();
});
