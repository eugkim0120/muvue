import { render, screen, fireEvent } from "@testing-library/preact";
import { Sheet } from "../src/ui/Sheet";
import { Segmented } from "../src/ui/Segmented";
import { Pill } from "../src/ui/Pill";
import { Button } from "../src/ui/Button";

test("Sheet closes on Escape and on the close button", () => {
  const onClose = vi.fn();
  render(<Sheet title="t" onClose={onClose}>body</Sheet>);
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByLabelText("close"));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test("Sheet moves focus in on open, traps Tab, and restores focus on close", () => {
  const opener = document.createElement("button");
  document.body.appendChild(opener);
  opener.focus();
  const { unmount } = render(<Sheet title="t" onClose={() => {}}><input aria-label="a" /><input aria-label="b" /></Sheet>);
  const close = screen.getByLabelText("close");
  const b = screen.getByLabelText("b");
  expect(document.activeElement).toBe(close);
  b.focus();
  fireEvent.keyDown(b, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(b);
  unmount();
  expect(document.activeElement).toBe(opener);
  opener.remove();
});

test("Segmented reports the chosen value", () => {
  const onChange = vi.fn();
  render(<Segmented options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} value="a" onChange={onChange} />);
  fireEvent.click(screen.getByText("B"));
  expect(onChange).toHaveBeenCalledWith("b");
});

test("Pill shows the status in words", () => {
  render(<Pill status="in_progress" />);
  expect(screen.getByText("in progress")).toHaveClass("st-in_progress");
});

test("Button busy renders a spinner and disables", () => {
  const { container } = render(<Button busy>Save</Button>);
  expect(container.querySelector(".spinner")).toBeTruthy();
  expect(container.querySelector("button")).toBeDisabled();
});
