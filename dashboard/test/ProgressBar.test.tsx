import { render, screen } from "@testing-library/preact";
import { ProgressBar, progressCounts } from "../src/project/ProgressBar";

test("counts map statuses to done, running, review and blocked", () => {
  const c = progressCounts([{ status: "done" }, { status: "in_progress" }, { status: "review" }, { status: "awaiting_approval" }, { status: "failed" }, { status: "ready" }]);
  expect(c).toEqual({ done: 1, running: 1, review: 2, blocked: 1, total: 6 });
});

test("the bar is a progressbar with value semantics and a full text alternative, and spend sits beside it", () => {
  render(<ProgressBar counts={{ done: 2, running: 1, review: 1, blocked: 0, total: 5 }} spend="$3.20 of $10.00 (claude)" />);
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveAccessibleName("2 done, 1 running, 1 in review, 0 blocked, of 5 tasks");
  expect(bar.getAttribute("aria-valuenow")).toBe("2");
  expect(bar.getAttribute("aria-valuemin")).toBe("0");
  expect(bar.getAttribute("aria-valuemax")).toBe("5");
  expect(screen.getByText("$3.20 of $10.00 (claude)")).toBeTruthy();
});

test("the counts are also visible as text, not only as segment colour", () => {
  render(<ProgressBar counts={{ done: 2, running: 1, review: 1, blocked: 0, total: 5 }} spend={null} />);
  expect(screen.getByText("2 done · 1 running · 1 in review · 0 blocked")).toBeTruthy();
});

test("no spend label means no spend element", () => {
  const { container } = render(<ProgressBar counts={{ done: 1, running: 0, review: 0, blocked: 0, total: 1 }} spend={null} />);
  expect(container.querySelector(".progress-spend")).toBeNull();
});

test("no tasks renders nothing", () => {
  const { container } = render(<ProgressBar counts={{ done: 0, running: 0, review: 0, blocked: 0, total: 0 }} spend={null} />);
  expect(container.firstChild).toBeNull();
});
