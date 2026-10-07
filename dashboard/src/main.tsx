import "./styles/tokens.css";
import "./styles/base.css";
import "./ui/ui.css";
import "./shell/shell.css";
import "./canvas/canvas.css";
import "./cards/cards.css";
import { render } from "preact";
import { App } from "./app";
import { exchangeFragmentNonce, applyAuthResult, handleLinkHashChange, loadWhoami } from "./api/auth";
import { onForbidden, api } from "./api/client";
import { routes } from "./api/routes";
import { connectStream } from "./api/stream";
import { resetRouteFromLocation } from "./router";
import { authed, setSignedOut, loadProjects, protocolVersion, refreshTick, toastError } from "./state";
import { effect } from "@preact/signals";

// An expired session is always worth saying. Otherwise only a page that
// thought it was signed in changes state: a refused paste attempt
// reports itself in the sign-in sheet instead.
onForbidden((problem) => {
  if (problem === "expired") setSignedOut("expired");
  else if (authed.value) setSignedOut(problem === "invalid" ? "stale" : "read_only");
});

window.addEventListener("hashchange", () => { handleLinkHashChange().catch(toastError); });

async function boot() {
  applyAuthResult(await exchangeFragmentNonce());
  if (authed.value) await loadWhoami();
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
