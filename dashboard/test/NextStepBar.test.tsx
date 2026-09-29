import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { NextStepBar } from "../src/project/NextStepBar";
import * as client from "../src/api/client";
import { launches } from "../src/project/activity";

test("approve_tasks approves gate2 by project id", async () => {
  const post = vi.spyOn(client, "post").mockResolvedValue({});
  render(<NextStepBar step={{ id: "approve_tasks", title: "Approve the task list", detail: "d" }} authed projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  fireEvent.click(screen.getByText("Approve task list"));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/nodes/7/approve", { target: "gate2" }));
});

test("read-only viewers see how to act instead of buttons", () => {
  render(<NextStepBar step={{ id: "approve_spec", title: "Approve the spec", detail: "d" }} authed={false} projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.getByText("Sign in to act")).toBeTruthy();
});

test("plan_tasks offers both ways to create tasks", () => {
  launches.value = [];
  render(<NextStepBar step={{ id: "plan_tasks", title: "Plan the tasks", detail: "d" }} authed projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  expect(screen.getByText("✨ Plan tasks with agent")).toBeTruthy();
  expect(screen.getByText("+ Add task myself")).toBeTruthy();
});

test("Important #4: while a breakdown launch for this spec is in flight, '+ Add task myself' is disabled too, closing the double-tap race", () => {
  launches.value = [{ kind: "breakdown", nodeId: 1, projectId: 7, label: "planning", afterEventId: 0, at: Date.now() }];
  render(<NextStepBar step={{ id: "plan_tasks", title: "Plan the tasks", detail: "d" }} authed projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  expect(screen.getByText("+ Add task myself").closest("button")).toBeDisabled();
  launches.value = [];
});
