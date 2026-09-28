import "./styles/tokens.css";
import "./styles/base.css";
import "./ui/ui.css";
import "./shell/shell.css";
import "./canvas/canvas.css";
import "./cards/cards.css";
import { render } from "preact";
import { App } from "./app";
import { exchangeFragmentNonce } from "./api/auth";
import { onForbidden, api } from "./api/client";
import { routes } from "./api/routes";
import { connectStream } from "./api/stream";
import { resetRouteFromLocation } from "./router";
import { setAuthed, loadProjects, protocolVersion, refreshTick, toastError } from "./state";
import { effect } from "@preact/signals";

onForbidden(() => setAuthed(false));

async function boot() {
  setAuthed(await exchangeFragmentNonce());
  resetRouteFromLocation();
  const h = await api<{ protocol_version: number }>(routes.healthz());
  protocolVersion.value = h.protocol_version;
  effect(() => {
    void refreshTick.value;
    loadProjects().catch(toastError);
  });
  connectStream();
  render(<App />, document.getElementById("root")!);
}

boot().catch((e) => {
  document.getElementById("root")!.textContent = "muvue dashboard failed to start: " + (e instanceof Error ? e.message : String(e));
});
