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
import { buildAuthHeaders, buildWebviewHtml, DEFAULT_DAEMON_URL, joinUrl, resolveEndpoint } from "./lib";

const SESSION_TOKEN_KEY = "muvue.sessionToken";

function getDaemonUrl(): string {
  return vscode.workspace.getConfiguration("muvue").get<string>("daemonUrl", DEFAULT_DAEMON_URL);
}

/**
 * `muvue serve` prints the human-verb session token once at startup and
 * writes it to `<repo>/.muvue/session` (`core.daemon.create_session`); the
 * extension does not read that file (it is `0600`, repo-local, and the
 * extension may run against a remote/forwarded daemon), so it asks once
 * and remembers the answer in `SecretStorage` for this VS Code install.
 */
async function getSessionToken(context: vscode.ExtensionContext): Promise<string | undefined> {
  const existing = await context.secrets.get(SESSION_TOKEN_KEY);
  if (existing) return existing;
  const entered = await vscode.window.showInputBox({
    prompt: "muvue session token (printed by `muvue serve`, or in <repo>/.muvue/session)",
    password: true,
    ignoreFocusOut: true,
  });
  if (entered) await context.secrets.store(SESSION_TOKEN_KEY, entered);
  return entered || undefined;
}

async function promptForId(what: string): Promise<number | undefined> {
  const raw = await vscode.window.showInputBox({
    prompt: `${what} id`,
    validateInput: (v) => (/^\d+$/.test(v.trim()) ? undefined : "enter a numeric id"),
  });
  return raw ? Number(raw.trim()) : undefined;
}

/** Shared by `approveNode` and `pauseProject`: resolves the endpoint,
 * calls the daemon with the stored session token, and reports the result.
 * The daemon call itself is a plain `fetch` (no HTTP client dependency,
 * per plan working rule 2). */
async function callHumanVerb(
  context: vscode.ExtensionContext,
  command: "approveNode" | "pauseProject",
  what: string,
): Promise<void> {
  const id = await promptForId(what);
  if (id === undefined) return;
  const token = await getSessionToken(context);
  if (!token) {
    vscode.window.showWarningMessage("muvue: no session token entered, cancelled.");
    return;
  }
  const { method, path } = resolveEndpoint(command, id);
  const url = joinUrl(getDaemonUrl(), path);
  try {
    const response = await fetch(url, { method, headers: buildAuthHeaders(token) });
    if (!response.ok) {
      const body = await response.text();
      vscode.window.showErrorMessage(`muvue: ${method} ${path} -> ${response.status}: ${body}`);
      return;
    }
    vscode.window.showInformationMessage(`muvue: ${method} ${path} -> ${response.status} OK`);
  } catch (err) {
    vscode.window.showErrorMessage(`muvue: could not reach daemon at ${url}: ${String(err)}`);
  }
}

/**
 * Opens (or reveals) the webview panel. The panel's own HTML is just an
 * `<iframe>` (`buildWebviewHtml`) pointed at the daemon's real `/` URL --
 * the dashboard rendered is P2/P7's actual `index.html`, served live by
 * the daemon, not a bundled copy (P8 acceptance #1). The extension does
 * not start `muvue serve` itself (decision #66); it assumes one is
 * already running at `muvue.daemonUrl`.
 */
function openDashboard(context: vscode.ExtensionContext): void {
  const daemonUrl = getDaemonUrl();
  const panel = vscode.window.createWebviewPanel(
    "muvueDashboard",
    "muvue dashboard",
    vscode.ViewColumn.One,
    { enableScripts: false, retainContextWhenHidden: true },
  );
  panel.webview.html = buildWebviewHtml(daemonUrl);
  context.subscriptions.push(panel);
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("muvue.openDashboard", () => openDashboard(context)),
    vscode.commands.registerCommand("muvue.approveNode", () =>
      callHumanVerb(context, "approveNode", "node"),
    ),
    vscode.commands.registerCommand("muvue.pauseProject", () =>
      callHumanVerb(context, "pauseProject", "project"),
    ),
  );
}

export function deactivate(): void {
  // No daemon process, timers, or listeners owned by this extension to
  // tear down: it is a pure thin client (plan working rule 3).
}
