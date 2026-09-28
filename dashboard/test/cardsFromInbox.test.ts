import { cardsFromInbox, type Inbox } from "../src/cards/cardsFromInbox";
import type { NodeRow } from "../src/state";

const emptyInbox: Inbox = { questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] };
const nodesById = { 5: { title: "Detect pitch", project_id: 1 } };

test("a pending spec synthesizes a spec_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: { id: 1, title: "Voxscore", body_md: null, status: "pending", agent: null }, taskCount: 0, projectPhase: "planning", revisions: [], nodesById, projectId: 1 });
  expect(cards.map((c) => c.kind)).toEqual(["spec_review"]);
  expect(cards[0]!.nodeId).toBe(1);
});

test("an approved spec with tasks in planning phase synthesizes a gate2_review card", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: { id: 1, title: "Voxscore", body_md: null, status: "ready", agent: null }, taskCount: 2, projectPhase: "planning", revisions: [], nodesById, projectId: 1 });
  expect(cards.map((c) => c.kind)).toEqual(["gate2_review"]);
});

test("a question maps to a question card with the node's title resolved from nodesById", () => {
  const inbox: Inbox = { ...emptyInbox, questions: [{ id: 9, node_id: 5, text: "which encoder?", default_answer: "opus", default_ok: true }] };
  const cards = cardsFromInbox(inbox, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById, projectId: 1 });
  expect(cards[0]).toMatchObject({ kind: "question", nodeId: 5, title: "Detect pitch", context: "which encoder?" });
});

test("review/blocked nodes map to task_review/blocked cards", () => {
  const node: NodeRow = { id: 5, project_id: 1, parent_id: null, kind: "task", title: "Detect pitch", status: "review", risk_tier: "low", owner: "claude" };
  const cards = cardsFromInbox({ ...emptyInbox, review: [node] }, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById, projectId: 1 });
  expect(cards[0]).toMatchObject({ kind: "task_review", nodeId: 5, agent: "claude" });
});

test("an unapproved revision synthesizes a revision card; an approved one does not", () => {
  const cards = cardsFromInbox(emptyInbox, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [{ n: 1, approved_at: null }, { n: 2, approved_at: "2026-01-01" }], nodesById, projectId: 1 });
  expect(cards.map((c) => c.kind)).toEqual(["revision"]);
  expect(cards[0]!.nodeId).toBe(null);
  expect(cards[0]!.projectId).toBe(1);
});

test("structure/audit/signal/unattributed events all collapse into ack cards", () => {
  const ev = { id: 1, ts: "t", node_id: null, project_id: 1, type: "inbox.structure_update_ready", payload: "{}", acked_at: null };
  const cards = cardsFromInbox({ ...emptyInbox, structure_updates: [ev] }, { spec: null, taskCount: 0, projectPhase: "executing", revisions: [], nodesById, projectId: 1 });
  expect(cards[0]!.kind).toBe("ack");
});
