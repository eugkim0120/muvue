import { render, screen, waitFor } from "@testing-library/preact";
import { AgentsSheet } from "../src/project/AgentsSheet";
import * as client from "../src/api/client";

test("lists each agent's roles, current work, runs today, and spend", async () => {
  vi.spyOn(client, "api").mockResolvedValue({ agents: [
    { name: "claude", roles: ["task", "subtask"], current: { node_id: 5, title: "Detect pitch", elapsed_s: 90 }, runs_today: 3, spend: 1.2, budget: 10, last_error: null },
    { name: "codex", roles: ["spec"], current: null, runs_today: 0, spend: 0, budget: 10, last_error: "rate limited" },
  ] });
  render(<AgentsSheet projectId={1} onClose={() => {}} />);
  await waitFor(() => screen.getByText("claude"));
  expect(screen.getByText(/Detect pitch/)).toBeTruthy();
  expect(screen.getByText("idle")).toBeTruthy();
  expect(screen.getByText("rate limited")).toBeTruthy();
});
