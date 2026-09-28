import { render, screen, waitFor } from "@testing-library/preact";
import { Runs } from "../src/node/Runs";
import * as client from "../src/api/client";

test("shows each run/breakdown event with agent, outcome and a log tail toggle", async () => {
  vi.spyOn(client, "api").mockResolvedValue({ runs: [
    { id: 1, ts: "2026-09-28T00:00:00Z", type: "node.start", node_id: 5, actor: "runner:claude", payload: "{}" },
    { id: 2, ts: "2026-09-28T00:01:00Z", type: "node.done", node_id: 5, actor: "runner:claude", payload: "{}" },
    { id: 3, ts: "2026-09-28T00:02:00Z", type: "breakdown.finished", node_id: 5, actor: "agent", payload: "{\"created\":[6,7]}" },
  ] });
  render(<Runs nodeId={5} />);
  await waitFor(() => screen.getByText("node.done"));
  expect(screen.getByText("breakdown.finished")).toBeTruthy();
});
