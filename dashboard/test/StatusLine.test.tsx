import { render, screen } from "@testing-library/preact";
import { StatusLine } from "../src/project/StatusLine";

test("shows phase, done/total, busy agent count, and turns amber past 80% of budget", () => {
  render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={8.5} budget={10} />);
  expect(screen.getByText(/executing/)).toBeTruthy();
  expect(screen.getByText(/3\/9 done/)).toBeTruthy();
  expect(screen.getByText(/2 agents busy/)).toBeTruthy();
  const { container } = render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={8.5} budget={10} />);
  expect(container.querySelector(".spend-amber")).toBeTruthy();
});

test("spend turns red at the budget cap", () => {
  const { container } = render(<StatusLine phase="executing" done={3} total={9} busyAgents={2} spend={10} budget={10} />);
  expect(container.querySelector(".spend-red")).toBeTruthy();
});
