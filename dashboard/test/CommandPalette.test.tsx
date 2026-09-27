import { render, screen, fireEvent } from "@testing-library/preact";
import { paletteItems, CommandPalette } from "../src/shell/CommandPalette";
import { authed, projects as projectsSignal, projectId } from "../src/state";

const nodes = [
  { id: 7, project_id: 1, parent_id: null, kind: "task", title: "Pitch detection", status: "ready" as const, risk_tier: "low" as const, owner: null },
  { id: 8, project_id: 1, parent_id: null, kind: "task", title: "MIDI export", status: "done" as const, risk_tier: "low" as const, owner: null },
];
const projects = [{ id: 1, goal: "voxscore", phase: "planning" as const }, { id: 2, goal: "other", phase: "closed" as const }];
const actions = [{ label: "Pause", run: () => {} }];

test("matches tasks by number and by title, case-insensitive", () => {
  expect(paletteItems("7", nodes, projects, actions).map((i) => i.label)).toEqual(["#7 Pitch detection"]);
  expect(paletteItems("midi", nodes, projects, actions).map((i) => i.label)).toEqual(["#8 MIDI export"]);
});

test("empty query lists actions first, then tasks, then projects", () => {
  const labels = paletteItems("", nodes, projects, actions).map((i) => i.label);
  expect(labels[0]).toBe("Pause");
  expect(labels).toContain("#7 Pitch detection");
  expect(labels).toContain("Switch to #2 other");
});

test("Pause in the palette opens a confirm sheet instead of posting immediately, and offers Close project", async () => {
  authed.value = true;
  projectsSignal.value = [{ id: 1, goal: "voxscore", phase: "planning" }];
  projectId.value = 1;
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => [], text: async () => "" };
  }));
  render(<CommandPalette onClose={() => {}} />);
  await screen.findByText("Pause");
  expect(screen.getByText("Close project…")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Pause"));
  expect(await screen.findByText("Pause project?")).toBeInTheDocument();
  expect(calls.some((u) => u.includes("/pause"))).toBe(false);
  vi.unstubAllGlobals();
  authed.value = false;
  projectsSignal.value = [];
  projectId.value = null;
});
