import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";

test("renders the app title", () => {
  render(<App />);
  expect(screen.getByText("muvue")).toBeInTheDocument();
});
