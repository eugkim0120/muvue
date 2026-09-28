import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";

export function SpecBody({ nodeId, bodyMd, notes }: { nodeId: number; bodyMd: string | null; notes: { kind: string; text: string }[] }) {
  const [openLine, setOpenLine] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [general, setGeneral] = useState("");
  async function send(text: string, line?: number) {
    if (!text.trim()) return;
    try {
      await post(routes.nodeComment(nodeId), line === undefined ? { text } : { text, line });
      toast("comment saved"); setDraft(""); setGeneral(""); setOpenLine(null); refresh();
    } catch (e) { toastError(e); }
  }
  const byLine: Record<number, string[]> = {};
  const generalNotes: string[] = [];
  for (const note of notes.filter((n) => n.kind === "feedback")) {
    const m = note.text.match(/^\[L(\d+)\] ([\s\S]*)$/);
    if (m) (byLine[Number(m[1])] ??= []).push(m[2]!); else generalNotes.push(note.text);
  }
  const lines = (bodyMd || "").split("\n");
  return (
    <div class="stack">
      <div class="caption">Tap a line to comment on it. The agent sees comments in its brief.</div>
      <div class="card spec-body">
        {lines.map((text, i) => {
          const n = i + 1;
          return (
            <>
              <button type="button" class="spec-line" onClick={() => setOpenLine(openLine === n ? null : n)}>
                <span class="ln">{n}</span><span class="tx">{text || " "}</span>
              </button>
              {(byLine[n] ?? []).map((c) => <div class="callout spec-comment">{c}</div>)}
              {openLine === n && authed.value ? (
                <form class="spec-form row" onSubmit={(e) => { e.preventDefault(); void send(draft, n); }}>
                  <input placeholder={`comment on line ${n}`} value={draft} onInput={(e) => setDraft((e.target as HTMLInputElement).value)} />
                  <Button type="submit" variant="filled">Comment</Button>
                </form>
              ) : null}
            </>
          );
        })}
      </div>
      {generalNotes.length ? <section class="stack tight"><h3>General comments</h3>{generalNotes.map((c) => <div class="callout">{c}</div>)}</section> : null}
      {authed.value ? (
        <form class="stack tight" onSubmit={(e) => { e.preventDefault(); void send(general); }}>
          <textarea placeholder="general comment on this spec" value={general} onInput={(e) => setGeneral((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button type="submit">Comment</Button></div>
        </form>
      ) : null}
    </div>
  );
}
