import { cardsFromInbox, type Inbox } from "../src/cards/cardsFromInbox";
import type { NodeRow } from "../src/state";

const emptyInbox: Inbox = { questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] };
const nodesById = { 5: { title: "Detect pitch", project_id: 1 } };

test("a pending spec does not synthesize a spec_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { revisions: [], nodesById, projectId: 1 });
  expect(cards.some((c) => (c.kind as string) === "spec_review" || (c.kind as string) === "gate2_review")).toBe(false);
});

test("an approved spec with tasks in planning phase does not synthesize a gate2_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { revisions: [], nodesById, projectId: 1 });
  expect(cards.some((c) => (c.kind as string) === "spec_review" || (c.kind as string) === "gate2_review")).toBe(false);
});

test("a question maps to a question card with the node's title resolved from nodesById", () => {
  const inbox: Inbox = { ...emptyInbox, questions: [{ id: 9, node_id: 5, text: "which encoder?", default_answer: "opus", default_ok: true }] };
  const cards = cardsFromInbox(inbox, { revisions: [], nodesById, projectId: 1 });
  expect(cards[0]).toMatchObject({ kind: "question", nodeId: 5, title: "Detect pitch", context: "which encoder?" });
});

test("review/blocked nodes map to task_review/blocked cards", () => {
  const node: NodeRow = { id: 5, project_id: 1, parent_id: null, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "claude" };
  const cards = cardsFromInbox({ ...emptyInbox, review: [node] }, { revisions: [], nodesById, projectId: 1 });
  expect(cards[0]).toMatchObject({ kind: "task_review", nodeId: 5, ownerLabel: "claude" });
});

test("an unapproved revision synthesizes a revision card; an approved one does not", () => {
  const cards = cardsFromInbox(emptyInbox, { revisions: [{ n: 1, approved_at: null }, { n: 2, approved_at: "2026-01-01" }], nodesById, projectId: 1 });
  expect(cards.map((c) => c.kind)).toEqual(["revision"]);
  expect(cards[0]!.nodeId).toBe(null);
  expect(cards[0]!.projectId).toBe(1);
});

test("structure/audit/signal/unattributed events all collapse into ack cards", () => {
  const ev = { id: 1, ts: "t", node_id: null, project_id: 1, type: "inbox.structure_update_ready", payload: "{}", acked_at: null };
  const cards = cardsFromInbox({ ...emptyInbox, structure_updates: [ev] }, { revisions: [], nodesById, projectId: 1 });
  expect(cards[0]!.kind).toBe("ack");
});

test("a review card carries the agent's summary, the risk tier and a readable owner", () => {
  const inbox = { questions: [], review: [{ id: 5, project_id: 1, parent_id: 1, kind: "task", title: "Detect pitch", status: "review", risk_tier: "medium", owner: "runner:claude", summary: "Added a YIN tracker." }], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] } as never;
  const [card] = cardsFromInbox(inbox, { revisions: [], nodesById: {}, projectId: 1 });
  expect(card).toMatchObject({ kind: "task_review", context: "Added a YIN tracker.", tier: "medium", ownerLabel: "claude" });
});

test("a review card with no summary says so instead of repeating 'in review'", () => {
  const inbox = { questions: [], review: [{ id: 5, project_id: 1, parent_id: 1, kind: "task", title: "T", status: "review", risk_tier: "low", owner: null, summary: null }], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] } as never;
  expect(cardsFromInbox(inbox, { revisions: [], nodesById: {}, projectId: 1 })[0]!.context).toBe("No summary recorded");
});
