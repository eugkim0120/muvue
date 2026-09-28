import { render, screen, fireEvent } from "@testing-library/preact";
import { ActivityBar } from "../src/project/ActivityBar";

test("busy items show a spinner and elapsed seconds; errors can be dismissed", () => {
  const onDismiss = vi.fn();
  const { container } = render(
    <ActivityBar
      now={5000}
      items={[
        { tone: "busy", key: "a", text: "Starting planning…", startedAt: 1000, log: null },
        { tone: "error", key: "bd-fail:9", text: "Planning “v” failed: boom", log: { kind: "node", nodeId: 1 } },
      ]}
      onDismiss={onDismiss}
      onOpenLog={() => {}}
    />,
  );
  expect(container.querySelector("[data-activity]")).toBeTruthy();
  expect(container.querySelector(".spinner")).toBeTruthy();
  expect(screen.getByText("4s")).toBeTruthy();
  fireEvent.click(screen.getByText("Dismiss"));
  expect(onDismiss).toHaveBeenCalledWith("bd-fail:9");
});

test("renders nothing when idle", () => {
  const { container } = render(<ActivityBar now={0} items={[]} onDismiss={() => {}} onOpenLog={() => {}} />);
  expect(container.querySelector("[data-activity]")).toBeNull();
});
