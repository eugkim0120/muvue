import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";

test("renders the app title", () => {
  render(<App />);
  expect(screen.getAllByText("Plan").length).toBeGreaterThan(0);
});
