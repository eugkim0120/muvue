import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast } from "../state";
import type { CanvasSpec } from "./canvasData";
import { purposeLine } from "./canvasData";
import { openNode } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";
import { useAction } from "../ui/useAction";

export function SubmitSpecForm({ projectId }: { projectId: number }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const a = useAction();
  async function submit(e: Event) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    const ok = await a.run(() => post(routes.projectSpec(projectId), { title, body_md: body }));
    if (ok) { toast("spec submitted"); refresh(); }
  }
  return (
    <form class="spec-root-card stack" onSubmit={submit}>
      <input placeholder="title" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      <textarea placeholder="one requirement per line" value={body} onInput={(e) => setBody((e.target as HTMLTextAreaElement).value)} />
      {a.error ? <div class="callout danger">{a.error}</div> : null}
      <div class="actions"><Button type="submit" variant="filled" busy={a.busy} busyLabel="Submitting…">Submit spec</Button></div>
    </form>
  );
}

export function SpecRoot({ spec, style }: { spec: CanvasSpec; style?: Record<string, string | number> }) {
  return (
    <div class="spec-root-card" style={style} tabIndex={0} onClick={() => openNode(spec.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(spec.id); }}>
      <div class="row between" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
        <span class="title grow clamp-2">{spec.title}</span>
        <Pill status={spec.status} />
      </div>
      {spec.body_md ? <div class="caption clamp-1">{purposeLine(spec.body_md)}</div> : null}
      {spec.agent ? <div class="caption">{`planned by ${spec.agent}`}</div> : null}
    </div>
  );
}
