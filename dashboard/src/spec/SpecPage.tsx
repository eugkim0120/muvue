import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refreshTick, refresh, toast, toastError } from "../state";
import { route, navigate } from "../router";
import { Pill } from "../ui/Pill";
import { Button } from "../ui/Button";
import { Actions } from "../node/Actions";
import type { NodeDetail } from "../node/NodeSheet";

export function SpecPage() {
  const id = Number(route.value.params[0]);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openLine, setOpenLine] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [general, setGeneral] = useState("");

  useEffect(() => { api<NodeDetail>(routes.node(id)).then((d) => { setDetail(d); setError(null); }, (e) => setError(e.message)); }, [id, refreshTick.value]);

  async function send(text: string, line?: number) {
    if (!text.trim()) return;
    try {
      await post(routes.nodeComment(id), line === undefined ? { text } : { text, line });
      toast("comment saved"); setDraft(""); setGeneral(""); setOpenLine(null); refresh();
    } catch (e) { toastError(e); }
  }

  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!detail) return <div class="page"><p class="muted">loading…</p></div>;
  const byLine: Record<number, string[]> = {};
  const generalNotes: string[] = [];
  for (const note of detail.notes.filter((n) => n.kind === "feedback")) {
    const m = note.text.match(/^\[L(\d+)\] ([\s\S]*)$/);
    if (m) (byLine[Number(m[1])] ??= []).push(m[2]!); else generalNotes.push(note.text);
  }
  const lines = (detail.node.body_md || "").split("\n");
  return (
    <div class="page stack">
      <Button variant="plain" onClick={() => navigate("#/plan")}>‹ Plan</Button>
      <div class="row between"><h1 class="page-title">#{detail.node.id} {detail.node.title}</h1><Pill status={detail.node.status} /></div>
      <Actions detail={detail} onDone={() => navigate("#/plan")} />
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
