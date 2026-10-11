import { branchRows, stageReached, stateLabel, ageLabel, taskLabel, aheadBehindLabel, changesLabel, spendLabel } from "../src/agents/agentsData";
import { wt, session } from "./agentsFixtures";

test("branch rows: base first, live branches by fork point, the rest newest commit first", () => {
  const rows = branchRows({
    errors: [],
    worktrees: [
      wt({ path: "/repo", branch: "main", is_base: true, stage: null }),
      wt({ path: "/late", branch: "late", fork_time: 300, session_ids: ["a"] }),
      wt({ path: "/early", branch: "early", fork_time: 100, session_ids: ["b"] }),
      wt({ path: "/gone", branch: "gone", session_ids: ["c"], tip_time: 10 }),
      wt({ path: "/fresh", branch: "fresh", tip_time: 50 }),
    ],
  }, [session({ id: "a" }), session({ id: "b", state: "waiting" }), session({ id: "c", state: "ended" })]);
  expect(rows.base?.worktree.branch).toBe("main");
  expect(rows.active.map((r) => r.worktree.branch)).toEqual(["early", "late"]);
  expect(rows.active[0]!.session?.id).toBe("b");
  expect(rows.idle.map((r) => r.worktree.branch)).toEqual(["fresh", "gone"]);
});

test("the newest live session wins a worktree with two", () => {
  const rows = branchRows({ errors: [], worktrees: [wt({ path: "/x", session_ids: ["old", "new"] })] }, [
    session({ id: "old", last_event_at: "2026-10-10T10:00:00.000Z" }),
    session({ id: "new", last_event_at: "2026-10-10T11:00:00.000Z" }),
  ]);
  expect(rows.active[0]!.session?.id).toBe("new");
});

test("labels", () => {
  expect(stateLabel("waiting")).toBe("waiting on you");
  expect(stateLabel("quiet")).toBe("quiet");
  expect([stageReached(null), stageReached("worktree"), stageReached("review"), stageReached("merged")]).toEqual([0, 1, 4, 5]);
  expect(taskLabel(wt({ node_id: 12, node_title: "Detect pitch" }))).toBe("#12 Detect pitch");
  expect(taskLabel(wt({}))).toBe("off-plan");
  expect(aheadBehindLabel(wt({ ahead: 2, behind: 1 }))).toBe("↑2 ↓1");
  expect(aheadBehindLabel(wt({ ahead: null, behind: null }))).toBe("–");
  expect(changesLabel(wt({ changed_paths: ["a"] }))).toBe("1 file");
  expect(changesLabel(wt({ changed_paths: ["a", "b"] }))).toBe("2 files");
  expect(spendLabel({ in_tokens: 1000, out_tokens: 200, cost: 0.123 })).toBe("1200 tokens · $0.12");
  const now = Date.parse("2026-10-10T12:00:00Z");
  expect(ageLabel("2026-10-10T11:59:30.000Z", now)).toBe("just now");
  expect(ageLabel("2026-10-10T11:45:00.000Z", now)).toBe("15m ago");
  expect(ageLabel(now / 1000 - 3 * 3600, now)).toBe("3h ago");
  expect(ageLabel(now / 1000 - 2 * 86400, now)).toBe("2d ago");
});
