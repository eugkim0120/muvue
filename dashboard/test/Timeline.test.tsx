import { render, screen, waitFor } from "@testing-library/preact";
import { familyOf, groupByDay, Timeline } from "../src/activity/Timeline";
import { projectId } from "../src/state";
import { routes } from "../src/api/routes";

afterEach(() => { vi.unstubAllGlobals(); projectId.value = null; });

test("event families come from the type prefix", () => {
  expect(familyOf("node.done")).toBe("task");
  expect(familyOf("review.awaiting")).toBe("review");
  expect(familyOf("runner.rate_limited")).toBe("runner");
  expect(familyOf("commit.linked")).toBe("commit");
  expect(familyOf("question.asked")).toBe("question");
  expect(familyOf("project.phase_changed")).toBe("project");
  expect(familyOf("weird")).toBe("other");
});

test("events group by day, newest day first, newest event first within a day", () => {
  const evs = [
    { id: 1, ts: "2026-09-26T10:00:00", type: "node.created", actor: "human", actor_evidence: "tty", node_id: 1 },
    { id: 2, ts: "2026-09-27T09:00:00", type: "node.start", actor: "agent", actor_evidence: "x", node_id: 1 },
    { id: 3, ts: "2026-09-27T11:00:00", type: "node.done", actor: "agent", actor_evidence: "x", node_id: 1 },
  ];
  const groups = groupByDay(evs);
  expect(groups.map((g) => g.day)).toEqual(["2026-09-27", "2026-09-26"]);
  expect(groups[0]!.events.map((e) => e.id)).toEqual([3, 2]);
});

test("switching project ignores a slower, stale response from the previous project", async () => {
  const deferred: Record<string, (data: unknown) => void> = {};
  vi.stubGlobal("fetch", vi.fn((url: string) => new Promise((resolve) => {
    deferred[url] = (data: unknown) => resolve({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => data, text: async () => "" });
  })));

  projectId.value = 1;
  render(<Timeline />);
  const url1 = routes.events(1);
  await waitFor(() => expect(deferred[url1]).toBeDefined());

  projectId.value = 2;
  const url2 = routes.events(2);
  await waitFor(() => expect(deferred[url2]).toBeDefined());

  // the newer (project 2) request resolves first...
  deferred[url2]!([{ id: 20, ts: "2026-09-27T09:00:00", type: "node.start", actor: "agent", actor_evidence: "x", node_id: 5 }]);
  await screen.findByText((_, el) => el?.tagName === "SPAN" && el.textContent === "node.start · #5");

  // ...then the slower, stale project-1 response arrives and must be discarded.
  deferred[url1]!([{ id: 10, ts: "2026-09-26T10:00:00", type: "node.created", actor: "human", actor_evidence: "tty", node_id: 1 }]);
  await Promise.resolve();
  expect(screen.queryByText((_, el) => el?.tagName === "SPAN" && el.textContent === "node.created · #1")).toBeNull();
});
