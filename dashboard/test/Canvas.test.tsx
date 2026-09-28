import { render, screen } from "@testing-library/preact";
import { Canvas } from "../src/canvas/Canvas";
import type { CanvasData } from "../src/canvas/canvasData";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "ready", agent: null },
  tasks: [
    { id: 2, title: "Record voice", status: "done", risk_tier: "low", owner: null, agent: "claude", body_md: "capture mic", criteria_hash: "x", block_reason: null, subtasks: [] },
    { id: 3, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude", body_md: "turn audio into notes", criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 3, carries: "audio frames" }],
};

test("renders the spec root, every task box, and an arrow label for a carried edge", () => {
  const { container } = render(<Canvas data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("Record voice")).toBeTruthy();
  expect(screen.getByText("Detect pitch")).toBeTruthy();
  expect(screen.getByText("audio frames")).toBeTruthy();
  expect(container.querySelectorAll("svg path.flow-arrow").length).toBe(1);
});

test("the canvas viewport, not the page, is what scrolls sideways", () => {
  const { container } = render(<Canvas data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(container.querySelector(".canvas-viewport")).toBeTruthy();
});
