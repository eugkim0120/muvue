import { computed, signal } from "@preact/signals";
import { api } from "./api/client";
import { routes } from "./api/routes";

export type Phase = "planning" | "executing" | "paused" | "closed";
export type Project = { id: number; goal: string; phase: Phase };
export type NodeStatus = "pending" | "ready" | "in_progress" | "review" | "awaiting_approval" | "done" | "blocked" | "failed";
export type RiskTier = "low" | "medium" | "high";
export type NodeRow = {
  id: number; project_id: number; parent_id: number | null; kind: string; title: string;
  status: NodeStatus; risk_tier: RiskTier; owner: string | null; block_reason?: string | null;
};

export const projects = signal<Project[]>([]);
export const projectId = signal<number | null>(null);
export const currentProject = computed(() => projects.value.find((p) => p.id === projectId.value) ?? null);
export const authed = signal(false);
export const inboxCount = signal(0);
export const refreshTick = signal(0);
export const protocolVersion = signal<number | null>(null);

export function refresh(): void { refreshTick.value = refreshTick.value + 1; }
export function setAuthed(ok: boolean): void { authed.value = ok; }

export type Toast = { id: number; text: string; kind: "info" | "error" };
export const toasts = signal<Toast[]>([]);
let toastSeq = 0;
export function toast(text: string, kind: Toast["kind"] = "info"): void {
  const id = ++toastSeq;
  toasts.value = [...toasts.value, { id, text, kind }];
  setTimeout(() => { toasts.value = toasts.value.filter((t) => t.id !== id); }, 4000);
}
export function dismissToast(id: number): void { toasts.value = toasts.value.filter((t) => t.id !== id); }
export function toastError(e: unknown): void { toast(e instanceof Error ? e.message : String(e), "error"); }

// Picks the newest open project the first time, or when the chosen one
// disappears; otherwise keeps the user's choice across refreshes.
export async function loadProjects(): Promise<void> {
  const list = await api<Project[]>(routes.projects());
  projects.value = list;
  const chosen = list.find((p) => p.id === projectId.value);
  if (!chosen) {
    const open = list.filter((p) => p.phase !== "closed");
    projectId.value = list.length ? (open.length ? open[open.length - 1]! : list[list.length - 1]!).id : null;
  }
}
