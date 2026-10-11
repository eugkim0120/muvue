import { render, screen, fireEvent, within } from "@testing-library/preact";
import { App } from "../src/app";
import { parseHash, projectTabOf, setProjectTab, resetRouteFromLocation } from "../src/router";
import { projects, projectId } from "../src/state";

afterEach(() => { vi.restoreAllMocks(); });

test("the tab comes from ?tab= and defaults to the map", () => {
  expect(projectTabOf(parseHash("#/p/3"))).toBe("map");
  expect(projectTabOf(parseHash("#/p/3?tab=agents"))).toBe("agents");
  expect(projectTabOf(parseHash("#/p/3?tab=bogus"))).toBe("map");
});

test("setProjectTab keeps the page and the open node, and never writes the default", () => {
  window.location.hash = "#/p/3?node=7";
  setProjectTab("agents");
  expect(window.location.hash).toBe("#/p/3?node=7&tab=agents");
  setProjectTab("map");
  expect(window.location.hash).toBe("#/p/3?node=7");
});

test("a project has four tabs and Plan is a placeholder", () => {
  projects.value = [{ id: 4, goal: "g", phase: "executing" }];
  projectId.value = 4;
  window.location.hash = "#/p/4?tab=plan";
  resetRouteFromLocation();
  render(<App />);
  const nav = within(screen.getByRole("navigation", { name: "project views" }));
  expect(nav.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Map", "Plan", "Agents", "Repo"]);
  expect(nav.getByRole("tab", { name: "Plan" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(/Plan view is not built yet/)).toBeTruthy();
  fireEvent.click(nav.getByRole("tab", { name: "Repo" }));
  expect(window.location.hash).toBe("#/p/4?tab=repo");
});

test("with no project there are no tabs", () => {
  projects.value = [];
  projectId.value = null;
  window.location.hash = "#/";
  resetRouteFromLocation();
  render(<App />);
  expect(screen.queryByRole("navigation", { name: "project views" })).toBeNull();
});
