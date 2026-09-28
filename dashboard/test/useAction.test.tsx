import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { useAction } from "../src/ui/useAction";
import { Button } from "../src/ui/Button";

function Harness({ fn }: { fn: () => Promise<unknown> }) {
  const a = useAction();
  return (
    <div>
      <Button busy={a.busy} busyLabel="Starting…" onClick={() => void a.run(fn)}>Go</Button>
      {a.error ? <div role="alert">{a.error}</div> : null}
    </div>
  );
}

test("shows the busy label and blocks a second launch while in flight", async () => {
  let release!: () => void;
  const fn = vi.fn(() => new Promise<void>((r) => { release = r; }));
  render(<Harness fn={fn} />);
  fireEvent.click(screen.getByText("Go"));
  await waitFor(() => screen.getByText("Starting…"));
  expect(screen.getByRole("button")).toBeDisabled();
  expect(screen.getByRole("button")).toHaveAttribute("aria-busy", "true");
  fireEvent.click(screen.getByRole("button"));
  expect(fn).toHaveBeenCalledTimes(1);
  release();
  await waitFor(() => screen.getByText("Go"));
});

test("a failed action leaves its message on screen", async () => {
  render(<Harness fn={() => Promise.reject(new Error("unknown agent 'x'"))} />);
  fireEvent.click(screen.getByText("Go"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("unknown agent 'x'"));
});
