import { routes } from "../src/api/routes";

test("new canvas-backend routes are spelled correctly", () => {
  expect(routes.nodeChildren(7)).toBe("/nodes/7/children");
  expect(routes.nodeRemove(7)).toBe("/nodes/7/remove");
  expect(routes.nodeEdit(7)).toBe("/nodes/7/edit");
  expect(routes.nodeBreakdown(7)).toBe("/nodes/7/breakdown");
  expect(routes.projectRun(3)).toBe("/projects/3/run");
  expect(routes.agentsStatus(3)).toBe("/agents/status?project_id=3");
  expect(routes.nodeRuns(7)).toBe("/nodes/7/runs");
});
