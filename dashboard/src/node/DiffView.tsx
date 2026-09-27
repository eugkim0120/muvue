import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { classifyDiffLine } from "./diff";

const LABEL: Record<string, string> = { commits: "patches of the linked commits", worktree: "worktree against its branch point", none: "nothing committed yet" };

export function DiffView({ nodeId }: { nodeId: number }) {
  const [d, setD] = useState<{ source: string; diff: string; truncated: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<{ source: string; diff: string; truncated: boolean }>(routes.nodeDiff(nodeId)).then(setD, (e) => setError(e.message)); }, [nodeId]);
  if (error) return <div class="callout danger">{error}</div>;
  if (!d) return <p class="muted">loading…</p>;
  return (
    <div class="stack tight">
      <div class="caption">{LABEL[d.source] ?? d.source}{d.truncated ? " (truncated)" : ""}</div>
      <pre class="scroll-x">{d.diff ? d.diff.split("\n").map((line) => <span class={classifyDiffLine(line)}>{line + "\n"}</span>) : "(no diff)"}</pre>
    </div>
  );
}
