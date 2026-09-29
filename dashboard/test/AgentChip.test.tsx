import { render } from "@testing-library/preact";
import { AgentChip } from "../src/canvas/AgentChip";
import type { AgentState } from "../src/canvas/agentState";

const EXPECTED: Record<AgentState, string> = {
  unassigned: "no agent",
  waiting_for_plan_approval: "waits for approval",
  waiting_on_earlier: "waits for earlier tasks",
  ready: "ready to run",
  running: "working now",
  waiting_on_you: "needs you",
  done: "done",
  failed: "failed",
  paused: "paused",
};

test.each(Object.entries(EXPECTED))("%s reads as a short status line", (state, label) => {
  const { container } = render(<AgentChip agent="claude" state={state as AgentState} />);
  expect(container.querySelector(".agent-tag-text")!.textContent).toBe(`claude · ${label}`);
});

test("it is a status line, not a control: no .chip (44px button look), not focusable", () => {
  const { container } = render(<AgentChip agent="fake" state="ready" />);
  const tag = container.querySelector(".agent-tag")!;
  expect(tag.classList.contains("chip")).toBe(false);
  expect(tag.getAttribute("tabindex")).toBeNull();
  expect(tag.getAttribute("title")).toBe("fake · ready to run");
});

test("no agent shows the label alone; running pulses", () => {
  const { container, rerender } = render(<AgentChip agent={null} state="unassigned" />);
  expect(container.querySelector(".agent-tag-text")!.textContent).toBe("no agent");
  rerender(<AgentChip agent="claude" state="running" />);
  expect(container.querySelector(".agent-dot.pulse")).toBeTruthy();
});
