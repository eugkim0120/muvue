import { render, screen, fireEvent } from "@testing-library/preact";
import { Sheet } from "../src/ui/Sheet";
import { Segmented } from "../src/ui/Segmented";
import { Pill } from "../src/ui/Pill";

test("Sheet closes on Escape and on the close button", () => {
  const onClose = vi.fn();
  render(<Sheet title="t" onClose={onClose}>body</Sheet>);
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByLabelText("close"));
  expect(onClose).toHaveBeenCalledTimes(2);
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
