import { render, screen, waitFor } from "@testing-library/preact";
import { useApi } from "../src/hooks";

test("useApi resolves data and re-fetches when deps change, ignoring a stale response", async () => {
  let calls = 0;
  const fetcher = (id: number) => new Promise<{ id: number }>((resolve) => {
    calls++;
    // the first call resolves slower than the second, to prove the stale
    // response for id=1 never overwrites the fresh one for id=2
    setTimeout(() => resolve({ id }), id === 1 ? 20 : 0);
  });
  function Probe({ id }: { id: number }) {
    const { data, error } = useApi(() => fetcher(id), [id]);
    return <div>{error ? "error" : data ? `id:${data.id}` : "loading"}</div>;
  }
  const { rerender } = render(<Probe id={1} />);
  rerender(<Probe id={2} />);
  await waitFor(() => screen.getByText("id:2"));
  await new Promise((r) => setTimeout(r, 30));
  expect(screen.queryByText("id:1")).toBeNull();
  expect(calls).toBe(2);
});
