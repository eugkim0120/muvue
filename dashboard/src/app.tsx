import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";
import { route } from "./router";
import { authed } from "./state";
import { Sidebar } from "./shell/Sidebar";
import { TabBar } from "./shell/TabBar";
import { TopBar } from "./shell/TopBar";
import { TokenBanner } from "./shell/TokenBanner";
import { Toasts } from "./ui/Toasts";
import { PlanPage } from "./plan/PlanPage";
import { NodeSheet } from "./node/NodeSheet";

// Pages register here; Tasks 8-13 add their entries.
export const PAGES: Record<string, () => JSX.Element> = {
  plan: PlanPage,
};

export function App() {
  const [palette, setPalette] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const Page = PAGES[route.value.page] ?? PAGES["plan"]!;
  const nodeId = route.value.query.get("node");
  return (
    <div class="app">
      <Sidebar />
      <div class="stack" style={{ flex: 1, minWidth: 0, gap: 0 }}>
        <TopBar onSearch={() => setPalette(true)} />
        {authed.value ? null : <TokenBanner />}
        <main><Page /></main>
      </div>
      <TabBar />
      {nodeId ? <NodeSheet /> : null}
      {palette ? null : null}
      <Toasts />
    </div>
  );
}
