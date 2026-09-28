import { parseHash, navigate, openNode, closeNode, route, resetRouteFromLocation } from "../src/router";

test("empty hash has no page", () => {
  expect(parseHash("")).toMatchObject({ page: "", params: [] });
  expect(parseHash("#/")).toMatchObject({ page: "", params: [] });
});

test("path and query parse", () => {
  const r = parseHash("#/spec/3?node=7");
  expect(r.page).toBe("spec");
  expect(r.params).toEqual(["3"]);
  expect(r.query.get("node")).toBe("7");
});

test("the nonce fragment is never a route", () => {
  expect(parseHash("#n=abc").page).toBe("");
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

test("#/p/:id parses to page 'p' with the project id as the first param", () => {
  const r = parseHash("#/p/3");
  expect(r.page).toBe("p");
  expect(r.params).toEqual(["3"]);
});

test("#/p/3?node=9 carries the node query param", () => {
  const r = parseHash("#/p/3?node=9");
  expect(r.query.get("node")).toBe("9");
});

test("openNode reflects a navigate() called earlier in the same tick, not a stale route signal", () => {
  navigate("#/p/5");
  openNode(7);
  expect(window.location.hash).toBe("#/p/5?node=7");
});
