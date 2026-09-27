import { familyOf, groupByDay } from "../src/activity/Timeline";

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
