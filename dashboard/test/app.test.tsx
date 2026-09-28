import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";
import { route } from "../src/router";
import { projects, projectId } from "../src/state";

test("an old #/plan hash redirects to the current project's canvas", async () => {
  projects.value = [{ id: 4, goal: "g", phase: "planning" }];
  projectId.value = 4;
  window.location.hash = "#/plan";
  route.value = { page: "plan", params: [], query: new URLSearchParams() };
  render(<App />);
  await new Promise((r) => setTimeout(r, 0));
  expect(window.location.hash).toBe("#/p/4");
});

test("#/p/:id with no project selected still renders without throwing", () => {
  projects.value = [];
  projectId.value = null;
  window.location.hash = "#/";
  route.value = { page: "", params: [], query: new URLSearchParams() };
  render(<App />);
  expect(screen.getByText("Start a project")).toBeTruthy();
});
