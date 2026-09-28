import { render, screen, fireEvent } from "@testing-library/preact";
import { GhostBox } from "../src/canvas/GhostBox";

test("shows the typed title while pending, and a caption", () => {
  render(<GhostBox title="Export MusicXML" caption="creating…" />);
  expect(screen.getByText("Export MusicXML")).toBeTruthy();
  expect(screen.getByText("creating…")).toBeTruthy();
});

test("an error shows the message and a Retry button", () => {
  const onRetry = () => { onRetry.called = true; };
  onRetry.called = false;
  render(<GhostBox title="Export MusicXML" caption="creating…" error="network error" onRetry={onRetry} />);
  expect(screen.getByText("network error")).toBeTruthy();
  fireEvent.click(screen.getByText("Retry"));
  expect(onRetry.called).toBe(true);
});
