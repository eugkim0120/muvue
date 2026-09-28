import { render, screen } from "@testing-library/preact";
import { Canvas } from "../src/canvas/Canvas";
import type { CanvasData } from "../src/canvas/canvasData";
import { authed } from "../src/state";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "ready", agent: "fake" },
  tasks: [
    { id: 2, title: "Record voice", status: "done", risk_tier: "low", owner: null, agent: "claude", body_md: "capture mic", criteria_hash: "x", block_reason: null, subtasks: [] },
    { id: 3, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude", body_md: "turn audio into notes", criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 3, carries: "audio frames" }],
};
const props = { projectId: 1, projectPhase: "executing", needsYou: new Set<number>(), activity: null, onAddTask: () => {} };

test("renders spec and tasks as one DAG with a spec arrow, a dep arrow, and arrowheads", () => {
  const { container } = render(<Canvas data={data} {...props} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("audio frames")).toBeTruthy();
  expect(container.querySelectorAll("svg.dag path.flow-arrow")).toHaveLength(2);
  expect(container.querySelector("svg.dag marker#dag-arrowhead")).toBeTruthy();
  expect(container.querySelector(".task-box.running")).toBeTruthy();
});

test("every box has an explicit height so it cannot overlap its neighbours", () => {
  const { container } = render(<Canvas data={data} {...props} />);
  for (const el of container.querySelectorAll<HTMLElement>(".task-box, .spec-root-card")) expect(el.style.height).toMatch(/px$/);
});

test("no tasks: the placeholder offers both ways to create them", () => {
  authed.value = true;
  render(<Canvas data={{ ...data, tasks: [], edges: [] }} {...props} projectPhase="planning" />);
  expect(screen.getByText("No tasks yet")).toBeTruthy();
  expect(screen.getByText("✨ Plan tasks with agent")).toBeTruthy();
  expect(screen.getByText("+ Add task myself")).toBeTruthy();
  authed.value = false;
});

test("task boxes carry no inline action buttons", () => {
  authed.value = true;
  render(<Canvas data={data} {...props} />);
  expect(screen.queryByText("+ Subtask")).toBeNull();
  expect(screen.queryByText("✨")).toBeNull();
  authed.value = false;
});
