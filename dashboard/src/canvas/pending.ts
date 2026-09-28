import { signal } from "@preact/signals";

export type PendingCreate = { kind: "task" | "subtask"; parentId: number; title: string; error?: string };
export type PendingBreakdown = { nodeId: number; agent: string; log: string; startChildIds: Set<number> };

export const pendingCreates = signal<Record<string, PendingCreate>>({});
export const pendingBreakdowns = signal<Record<number, PendingBreakdown>>({});

export function startCreate(key: string, p: PendingCreate): void {
  pendingCreates.value = { ...pendingCreates.value, [key]: p };
}
export function resolveCreate(key: string): void {
  const { [key]: _removed, ...rest } = pendingCreates.value;
  pendingCreates.value = rest;
}
export function failCreate(key: string, error: string): void {
  const existing = pendingCreates.value[key];
  if (existing) pendingCreates.value = { ...pendingCreates.value, [key]: { ...existing, error } };
}

export function startBreakdown(nodeId: number, p: PendingBreakdown): void {
  pendingBreakdowns.value = { ...pendingBreakdowns.value, [nodeId]: p };
}
export function clearBreakdown(nodeId: number): void {
  const { [nodeId]: _removed, ...rest } = pendingBreakdowns.value;
  pendingBreakdowns.value = rest;
}
// A breakdown is done, for ghost-clearing purposes, once the node's live
// children outnumber the set present when the breakdown was launched.
// (`breakdown.finished`'s own event isn't polled directly here — the
// canvas already refetches on every SSE tick, so comparing child-id sets
// on that same tick is simpler than a second data source.)
export function shouldClearBreakdown(p: PendingBreakdown, currentChildIds: Set<number>): boolean {
  return currentChildIds.size > p.startChildIds.size;
}
