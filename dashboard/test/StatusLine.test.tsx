import { render } from "@testing-library/preact";
import { StatusLine } from "../src/project/StatusLine";

test("plain-language status with no invented spend", () => {
  const { container } = render(<StatusLine phase="planning" done={0} total={3} working={0} />);
  expect(container.textContent).toBe("Planning · 0 of 3 tasks done · no agent working");
  expect(container.textContent).not.toContain("$");
});
