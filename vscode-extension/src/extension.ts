/**
 * muvue VS Code extension (P8, plan section 11 row P8): a thin webview +
 * command bridge over the daemon's existing HTTP API (P2's `src/muvue/api/
 * app.py`). No business logic lives here -- see vscode-extension/README.md
 * and docs/decisions.md #66/#67 for scope decisions.
 *
 * This file only wires the `vscode` API to the pure logic in `./lib`; keep
 * it under 300 lines (plan section 11 acceptance for P8).
 */

import * as vscode from "vscode";
import {
  approveTarget,
  buildAuthHeaders,
  buildWebviewHtml,
  DEFAULT_DAEMON_URL,
  joinUrl,
  NONCE_PATH,
  resolveEndpoint,
  shouldReprompt,
} from "./lib";

/** Earlier versions kept the token in SecretStorage; it is removed on
 * activation because v4 section 8a keeps the token off disk. */
const LEGACY_SECRET_KEY = "muvue.sessionToken";

/** The session token, in this extension host's memory only. `muvue serve`
 * mints a new one on every start, so a remembered token would go stale
 * anyway; a 403 clears it and the next call asks again. */
let sessionToken: string | undefined;

function getDaemonUrl(): string {
  return vscode.workspace.getConfiguration("muvue").get<string>("daemonUrl", DEFAULT_DAEMON_URL);
}

async function getSessionToken(): Promise<string | undefined> {
  if (sessionToken) return sessionToken;
  const entered = await vscode.window.showInputBox({
    prompt: "muvue api token (printed by `muvue serve` as `api token: ...`)",
    password: true,
    ignoreFocusOut: true,
  });
  sessionToken = entered?.trim() || undefined;
  return sessionToken;
}

/** Calls the daemon with the session token. On a 403 the token is
 * dropped and the user is asked once more before giving up. */
async function callWithToken(
  path: string, method: "GET" | "POST" = "POST", body: object = {},
): Promise<Response | undefined> {
  const url = joinUrl(getDaemonUrl(), path);
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getSessionToken();
    if (!token) return undefined;
    const response = await fetch(url, {
      method,
      headers: buildAuthHeaders(token),
      body: method === "POST" ? JSON.stringify(body) : undefined,
    });
    if (!shouldReprompt(response.status)) return response;
    sessionToken = undefined;
  }
  vscode.window.showErrorMessage("muvue: the daemon rejected the token (403). Copy the current `api token` from `muvue serve`.");
  return undefined;
}

async function promptForId(what: string): Promise<number | undefined> {
  const raw = await vscode.window.showInputBox({
    prompt: `${what} id`,
    validateInput: (v) => (/^\d+$/.test(v.trim()) ? undefined : "enter a numeric id"),
  });
  return raw ? Number(raw.trim()) : undefined;
}

/** Shared by `approveNode` and `pauseProject`: resolves the endpoint,
 * calls the daemon with the session token, and reports the result. The
 * daemon call itself is a plain `fetch` (no HTTP client dependency, per
 * plan working rule 2). */
async function callHumanVerb(command: "approveNode" | "pauseProject", what: string): Promise<void> {
  const id = await promptForId(what);
  if (id === undefined) return;
  const { method, path } = resolveEndpoint(command, id);
  try {
    let body = {};
    if (command === "approveNode") {
      const node = await callWithToken(`/nodes/${id}`, "GET");
      if (!node) return;
      if (!node.ok) {
        vscode.window.showErrorMessage(`muvue: GET /nodes/${id} -> ${node.status}: ${await node.text()}`);
        return;
      }
      body = { target: approveTarget(((await node.json()) as { node: { status: string } }).node.status) };
    }
    const response = await callWithToken(path, "POST", body);
    if (!response) return;
    if (!response.ok) {
      const body = await response.text();
      vscode.window.showErrorMessage(`muvue: ${method} ${path} -> ${response.status}: ${body}`);
      return;
    }
    vscode.window.showInformationMessage(`muvue: ${method} ${path} -> ${response.status} OK`);
  } catch (err) {
    vscode.window.showErrorMessage(`muvue: could not reach daemon at ${getDaemonUrl()}: ${String(err)}`);
  }
}

/**
 * Opens the webview panel. The panel's own HTML is just an `<iframe>`
 * (`buildWebviewHtml`) pointed at the daemon's real `/` URL, so the
 * dashboard rendered is the daemon's actual `index.html` (P8 acceptance
 * #1). With a token, the extension first mints a one-time nonce for the
 * iframe so the dashboard can act; without one it opens read-only. The
 * extension does not start `muvue serve` itself (decision #66).
 */
async function openDashboard(context: vscode.ExtensionContext): Promise<void> {
  let nonce: string | undefined;
  try {
    const response = await callWithToken(NONCE_PATH);
    if (response?.ok) nonce = ((await response.json()) as { nonce?: string }).nonce;
  } catch (err) {
    vscode.window.showWarningMessage(`muvue: could not reach daemon at ${getDaemonUrl()}: ${String(err)}`);
  }
  const panel = vscode.window.createWebviewPanel(
    "muvueDashboard",
    "muvue dashboard",
    vscode.ViewColumn.One,
    // Scripts must be enabled: the webview's sandbox is inherited by the
    // nested iframe, so without `allow-scripts` the dashboard's own JS never
    // runs. The wrapper page itself still runs none (its CSP has no
    // script-src, see `buildWebviewHtml`).
    { enableScripts: true, retainContextWhenHidden: true },
  );
  panel.webview.html = buildWebviewHtml(getDaemonUrl(), nonce);
  context.subscriptions.push(panel);
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("muvue.openDashboard", () => openDashboard(context)),
    vscode.commands.registerCommand("muvue.approveNode", () => callHumanVerb("approveNode", "node")),
    vscode.commands.registerCommand("muvue.pauseProject", () => callHumanVerb("pauseProject", "project")),
  );
  context.secrets.delete(LEGACY_SECRET_KEY).then(undefined, (err) =>
    vscode.window.showWarningMessage(`muvue: could not remove the token an older version stored: ${String(err)}`),
  );
}

export function deactivate(): void {
  // No daemon process, timers, or listeners owned by this extension to
  // tear down: it is a pure thin client (plan working rule 3).
}
