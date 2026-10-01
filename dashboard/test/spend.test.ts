import { spendLabel } from "../src/project/spend";

test("no budgets configured means no spend label", () => {
  expect(spendLabel({ spend_by_driver: {} })).toBeNull();
  expect(spendLabel(null)).toBeNull();
});

test("a response without spend_by_driver means no spend label", () => {
  expect(spendLabel({} as never)).toBeNull();
});

test("names the driver closest to its limit and formats usd", () => {
  const k = { spend_by_driver: { a: { unit: "usd", spent: 1, limit: 10, pct: 0.1 }, b: { unit: "usd", spent: 3.2, limit: 10, pct: 0.32 } } };
  expect(spendLabel(k)).toBe("$3.20 of $10.00 (b)");
});

test("non-usd units are printed with their unit", () => {
  expect(spendLabel({ spend_by_driver: { c: { unit: "tokens", spent: 1200, limit: 5000, pct: 0.24 } } })).toBe("1200 of 5000 tokens (c)");
});

test("zero spent with a budget still shows the label", () => {
  expect(spendLabel({ spend_by_driver: { a: { unit: "usd", spent: 0, limit: 10, pct: 0 } } })).toBe("$0.00 of $10.00 (a)");
});

test("at or over budget is flagged in text", () => {
  expect(spendLabel({ spend_by_driver: { a: { unit: "usd", spent: 12, limit: 10, pct: 1.2 } } })).toBe("$12.00 of $10.00 (a) · over budget");
  expect(spendLabel({ spend_by_driver: { a: { unit: "tokens", spent: 5000, limit: 5000, pct: 1 } } })).toBe("5000 of 5000 tokens (a) · over budget");
});

test("a zero limit does not divide by zero or throw", () => {
  expect(spendLabel({ spend_by_driver: { a: { unit: "usd", spent: 0, limit: 0, pct: 0 } } })).toBe("$0.00 of $0.00 (a)");
});
