import { fireEvent, render, screen, cleanup } from "@testing-library/preact";
import { Canvas } from "../src/canvas/Canvas";
import { computeLayout } from "../src/canvas/flowLayout";
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
const props = { projectId: 1, projectPhase: "executing", needsYou: new Set<number>(), activity: null };

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

test("no tasks: the placeholder is caption-only -- NextStepBar owns the sole plan-tasks action (Important #4)", () => {
  authed.value = true;
  render(<Canvas data={{ ...data, tasks: [], edges: [] }} {...props} projectPhase="planning" />);
  expect(screen.getByText("No tasks yet")).toBeTruthy();
  expect(screen.queryByText("✨ Plan tasks with agent")).toBeNull();
  expect(screen.queryByText("+ Add task myself")).toBeNull();
  authed.value = false;
});

test("task boxes carry no inline action buttons", () => {
  authed.value = true;
  render(<Canvas data={data} {...props} />);
  expect(screen.queryByText("+ Subtask")).toBeNull();
  expect(screen.queryByText("✨")).toBeNull();
  authed.value = false;
});

test("with more than 12 tasks the finished ones fold into a '13 done' toggle that expands them", () => {
  const tasks = Array.from({ length: 14 }, (_, i) => ({ id: i + 10, title: "T" + i, status: i < 13 ? "done" : "ready", risk_tier: "low", owner: null, agent: "claude", body_md: null, criteria_hash: null, block_reason: null, subtasks: [] })) as never;
  const big = { spec: { id: 1, title: "S", body_md: null, status: "done", agent: null }, tasks, edges: [] } as CanvasData;
  const { container } = render(<Canvas data={big} {...props} />);
  expect(container.querySelectorAll(".task-box")).toHaveLength(1);
  fireEvent.click(screen.getByText("13 done"));
  expect(container.querySelectorAll(".task-box")).toHaveLength(14);
});

const chain = (n: number, doneCount = 0): CanvasData => ({
  spec: { id: 1, title: "S", body_md: null, status: "done", agent: null },
  tasks: Array.from({ length: n }, (_, i) => ({ id: i + 10, title: "T" + i, status: i < doneCount ? "done" : "ready", risk_tier: "low", owner: null, agent: "claude", body_md: null, criteria_hash: null, block_reason: null, subtasks: [] })) as never,
  edges: Array.from({ length: n - 1 }, (_, i) => ({ from: i + 10, to: i + 11, carries: null })),
});

test("a finished project over 12 tasks folds everything: toggle shows, no 'No tasks yet' placeholder", () => {
  const { container } = render(<Canvas data={chain(14, 14)} {...props} />);
  expect(screen.getByText("14 done")).toBeTruthy();
  expect(container.querySelector(".dag-empty")).toBeNull();
  expect(container.querySelectorAll(".task-box")).toHaveLength(0);
});

test("the done toggle round-trips and reports aria-expanded", () => {
  const { container } = render(<Canvas data={chain(14, 13)} {...props} />);
  const open = screen.getByText("13 done");
  expect(open.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(open);
  expect(container.querySelectorAll(".task-box")).toHaveLength(14);
  const close = screen.getByText("Hide finished");
  expect(close.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(close);
  expect(container.querySelectorAll(".task-box")).toHaveLength(1);
  expect(screen.getByText("13 done")).toBeTruthy();
});

describe("a diagram taller than its viewport", () => {
  const VIEW_W = 390, VIEW_H = 300;
  const proto = HTMLElement.prototype;
  const realW = Object.getOwnPropertyDescriptor(proto, "clientWidth");
  const realH = Object.getOwnPropertyDescriptor(proto, "clientHeight");
  const realRO = globalThis.ResizeObserver;
  beforeEach(() => {
    Object.defineProperty(proto, "clientWidth", { configurable: true, get() { return this.classList?.contains("canvas-viewport") ? VIEW_W : 0; } });
    Object.defineProperty(proto, "clientHeight", { configurable: true, get() { return this.classList?.contains("canvas-viewport") ? VIEW_H : 0; } });
    globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as never;
  });
  afterEach(() => {
    cleanup();
    if (realW) Object.defineProperty(proto, "clientWidth", realW); else delete (proto as never as Record<string, unknown>).clientWidth;
    if (realH) Object.defineProperty(proto, "clientHeight", realH); else delete (proto as never as Record<string, unknown>).clientHeight;
    globalThis.ResizeObserver = realRO;
  });
  const d = chain(14);
  const lay = computeLayout(d.spec, d.tasks, d.edges, false);
  const frame = (c: Element) => c.querySelector<HTMLElement>(".canvas-frame")!;
  const ty = (c: Element) => Number(/translate\([^,]+px, (-?[\d.]+)px\)/.exec(frame(c).style.transform)![1]);
  const scale = (c: Element) => Number(/scale\(([\d.]+)\)/.exec(frame(c).style.transform)![1]);

  test("the viewport is a focusable, labelled group and never scrolls natively", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    const vp = container.querySelector<HTMLElement>(".canvas-viewport")!;
    expect(vp.getAttribute("tabindex")).toBe("0");
    expect(vp.getAttribute("role")).toBe("group");
    expect(vp.getAttribute("aria-label")).toBeTruthy();
  });

  test("modifier-key combinations are left to the browser, not swallowed as pan keys", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    const vp = container.querySelector<HTMLElement>(".canvas-viewport")!;
    for (const mod of [{ altKey: true }, { ctrlKey: true }, { metaKey: true }]) {
      const notCancelled = fireEvent.keyDown(vp, { key: "ArrowDown", ...mod });
      expect(notCancelled).toBe(true);
      expect(fireEvent.keyDown(vp, { key: "Home", ...mod })).toBe(true);
    }
    expect(ty(container)).toBe(0);
  });

  test("a diagram that fits its view is not a tab stop", () => {
    const small = chain(1);
    const { container } = render(<Canvas data={small} {...props} />);
    const vp = container.querySelector<HTMLElement>(".canvas-viewport")!;
    expect(computeLayout(small.spec, small.tasks, small.edges, false).height).toBeLessThan(VIEW_H);
    expect(vp.hasAttribute("tabindex")).toBe(false);
    expect(vp.getAttribute("aria-label") ?? "").not.toMatch(/arrow keys/i);
  });

  test("arrow keys, Page keys, Home and End pan the diagram within its bounds", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    const vp = container.querySelector<HTMLElement>(".canvas-viewport")!;
    const bottom = -(lay.height * scale(container) - VIEW_H);
    expect(ty(container)).toBe(0);
    fireEvent.keyDown(vp, { key: "ArrowUp" });
    expect(ty(container)).toBe(0);
    fireEvent.keyDown(vp, { key: "ArrowDown" });
    expect(ty(container)).toBe(-40);
    fireEvent.keyDown(vp, { key: "PageDown" });
    expect(ty(container)).toBeLessThan(-40);
    fireEvent.keyDown(vp, { key: "End" });
    expect(ty(container)).toBeCloseTo(bottom, 5);
    fireEvent.keyDown(vp, { key: "ArrowDown" });
    expect(ty(container)).toBeCloseTo(bottom, 5);
    fireEvent.keyDown(vp, { key: "Home" });
    expect(ty(container)).toBe(0);
  });

  test("pan up/down buttons are the touch path", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    fireEvent.click(screen.getByLabelText("pan down"));
    expect(ty(container)).toBeLessThan(0);
    fireEvent.click(screen.getByLabelText("pan up"));
    expect(ty(container)).toBe(0);
  });

  test("focusing a box below the fold pans it into view", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    const boxes = container.querySelectorAll<HTMLElement>(".task-box");
    const last = boxes[boxes.length - 1]!;
    fireEvent(last, new FocusEvent("focusin", { bubbles: true }));
    const top = parseFloat(last.style.top) * scale(container) + ty(container);
    const bottom = (parseFloat(last.style.top) + parseFloat(last.style.height)) * scale(container) + ty(container);
    expect(top).toBeGreaterThanOrEqual(0);
    expect(bottom).toBeLessThanOrEqual(VIEW_H);
  });

  test("a stale pan never leaves the diagram off-screen after zooming out", () => {
    const { container } = render(<Canvas data={d} {...props} />);
    fireEvent.click(screen.getByLabelText("zoom in"));
    fireEvent.click(screen.getByLabelText("zoom in"));
    fireEvent.keyDown(container.querySelector(".canvas-viewport")!, { key: "End" });
    fireEvent.click(screen.getByLabelText("zoom out"));
    const z = scale(container);
    expect(ty(container)).toBeCloseTo(-(lay.height * z - VIEW_H), 5);
  });
});
