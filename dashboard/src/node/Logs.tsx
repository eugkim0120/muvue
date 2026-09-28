import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick } from "../state";
import { Button } from "../ui/Button";

export function Logs({ nodeId, path }: { nodeId?: number; path?: string }) {
  const [text, setText] = useState("loading…");
  const pre = useRef<HTMLPreElement>(null);
  function load() {
    api<string>(path ?? routes.nodeLogs(nodeId!)).then((t) => { setText(t || "(no driver output yet)"); requestAnimationFrame(() => { if (pre.current) pre.current.scrollTop = pre.current.scrollHeight; }); }, (e) => setText(e.message));
  }
  useEffect(load, [nodeId, path, refreshTick.value]);
  return (
    <div class="stack tight">
      <div class="row between"><span class="caption">last 200 lines</span><Button variant="plain" onClick={load}>Refresh</Button></div>
      <pre ref={pre} class="scroll-x logs">{text}</pre>
    </div>
  );
}
