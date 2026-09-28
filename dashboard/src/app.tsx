import { useEffect, useState } from "preact/hooks";
import { route, navigate, isLegacyRoute } from "./router";
import { authed, projectId } from "./state";
import { Sidebar } from "./shell/Sidebar";
import { TopBar } from "./shell/TopBar";
import { TokenBanner } from "./shell/TokenBanner";
import { Toasts } from "./ui/Toasts";
import { ProjectPage } from "./project/ProjectPage";
import { NodeSheet } from "./node/NodeSheet";
import { CommandPalette } from "./shell/CommandPalette";

export function App() {
  const [palette, setPalette] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (isLegacyRoute(route.value) && projectId.value !== null) navigate("#/p/" + projectId.value);
  }, [route.value.page, projectId.value]);
  const nodeId = route.value.query.get("node");
  return (
    <div class="app">
      <Sidebar />
      <div class="stack" style={{ flex: 1, minWidth: 0, gap: 0 }}>
        <TopBar onSearch={() => setPalette(true)} />
        {authed.value ? null : <TokenBanner />}
        <main><ProjectPage /></main>
      </div>
      {nodeId ? <NodeSheet key={nodeId} /> : null}
      {palette ? <CommandPalette onClose={() => setPalette(false)} /> : null}
      <Toasts />
    </div>
  );
}
