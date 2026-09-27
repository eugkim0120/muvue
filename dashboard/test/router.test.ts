import { parseHash, navigate, openNode, closeNode, route, resetRouteFromLocation } from "../src/router";

test("empty hash is the plan page", () => {
  expect(parseHash("")).toMatchObject({ page: "plan", params: [] });
  expect(parseHash("#/")).toMatchObject({ page: "plan", params: [] });
});

test("path and query parse", () => {
  const r = parseHash("#/spec/3?node=7");
  expect(r.page).toBe("spec");
  expect(r.params).toEqual(["3"]);
  expect(r.query.get("node")).toBe("7");
});

test("the nonce fragment is never a route", () => {
  expect(parseHash("#n=abc").page).toBe("plan");
});

test("openNode keeps the page and closeNode drops the query", () => {
  window.location.hash = "#/inbox";
  resetRouteFromLocation();
  openNode(7);
  expect(window.location.hash).toBe("#/inbox?node=7");
  closeNode();
  expect(window.location.hash).toBe("#/inbox");
});

test("navigate updates the route signal", async () => {
  navigate("#/spend");
  await new Promise((r) => setTimeout(r, 0));
  resetRouteFromLocation();
  expect(route.value.page).toBe("spend");
});
