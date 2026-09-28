// dashboard/test/PhoneFlow.test.tsx
import { render, screen } from "@testing-library/preact";
import { PhoneFlow } from "../src/canvas/PhoneFlow";
import type { CanvasData } from "../src/canvas/canvasData";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "x", status: "ready", agent: null },
  tasks: [
    { id: 2, title: "a", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
    { id: 3, title: "b", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
    { id: 4, title: "c", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 4, carries: null }, { from: 3, to: 4, carries: null }],
};

test("stacks steps top to bottom and labels a parallel step", () => {
  const { container } = render(<PhoneFlow data={data} projectId={1} projectPhase="executing" needsYou={new Set()} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("parallel")).toBeTruthy();
  const steps = container.querySelectorAll(".phone-step");
  expect(steps.length).toBe(2);
});
