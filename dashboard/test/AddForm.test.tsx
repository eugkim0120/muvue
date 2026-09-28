import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AddForm } from "../src/canvas/AddForm";
import * as client from "../src/api/client";

const candidates = [{ id: 2, title: "Record voice", status: "ready", risk_tier: "low", owner: null, agent: null, body_md: null, criteria_hash: null, block_reason: null, subtasks: [] }] as const;

test("submitting posts title, body, criteria lines, and depends_on with carries", async () => {
  const spy = vi.spyOn(client, "post").mockResolvedValue({ node: { id: 9 } });
  render(<AddForm parentId={1} kind="task" candidates={candidates as any} onClose={() => {}} />);
  fireEvent.input(screen.getByPlaceholderText("title"), { target: { value: "Export MusicXML" } });
  fireEvent.input(screen.getByPlaceholderText("purpose"), { target: { value: "write the file" } });
  fireEvent.input(screen.getByPlaceholderText("one per line"), { target: { value: "writer passes\nvalid file" } });
  fireEvent.click(screen.getByLabelText("receives from Record voice"));
  fireEvent.input(screen.getByPlaceholderText("carrying ___"), { target: { value: "notes" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(spy).toHaveBeenCalled());
  expect(spy.mock.calls[0]![0]).toBe("/nodes/1/children");
  expect(spy.mock.calls[0]![1]).toEqual({
    title: "Export MusicXML", body_md: "write the file", criteria: ["writer passes", "valid file"],
    depends_on: [{ id: 2, carries: "notes" }], predicted_touches: [],
  });
});

test("Save shows Saving… while the create request is in flight", async () => {
  let release!: (v: unknown) => void;
  vi.spyOn(client, "post").mockImplementation(() => new Promise((r) => { release = r; }));
  render(<AddForm parentId={1} kind="task" candidates={[]} onClose={() => {}} />);
  fireEvent.input(screen.getByPlaceholderText("title"), { target: { value: "Record voice" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => screen.getByText("Saving…"));
  release({ node: { id: 2 } });
});
