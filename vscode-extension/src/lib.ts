/**
 * Pure logic for the muvue VS Code extension (P8, plan section 11 row P8).
 *
 * Deliberately importless of `vscode`: this module is unit-testable with
 * plain `node` (see test/lib.test.ts), since a real VS Code Extension Host
 * is not available in this build environment (see
 * vscode-extension/README.md and docs/decisions.md #66).
 */

/** Default daemon URL, matching `muvue serve`'s default `--host`/`--port`
 * (src/muvue/cli/main.py `serve`). Overridable via the `muvue.daemonUrl`
 * setting -- the extension never spawns the daemon itself (decision #66). */
export const DEFAULT_DAEMON_URL = "http://127.0.0.1:8765";

/** Mints a one-time dashboard nonce (`POST /auth/nonce`, session-gated). */
export const NONCE_PATH = "/auth/nonce";

/**
 * Builds the webview's own (wrapper) HTML: a single `<iframe>` pointed at
 * the daemon's real HTTP URL, so the dashboard the iframe renders is
 * whatever `GET /` on the running daemon serves right now -- never a
 * bundled copy (P8 acceptance #1).
 *
 * With a `nonce` (minted by the daemon for this panel), the iframe URL
 * carries it as `#n=<nonce>`. The dashboard exchanges it once. Because
 * the iframe is cross-site to the webview, its SameSite=Strict cookie is
 * never sent, so the page asks the exchange for the token and keeps it in
 * memory. Without a nonce the dashboard opens read-only.
 *
 * The CSP is scoped to that one origin: `frame-src` allows only the
 * daemon's origin, `script-src 'none'` since this wrapper page runs no
 * script of its own (all dashboard JS runs inside the iframe, same-origin
 * with the daemon, and is unaffected by this outer CSP).
 */
export function buildWebviewHtml(daemonUrl: string, nonce?: string): string {
  const origin = new URL(daemonUrl).origin;
  const src = nonce ? `${daemonUrl.replace(/\/+$/, "")}/#n=${encodeURIComponent(nonce)}` : daemonUrl;
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline';">
<style>html, body, iframe { margin: 0; padding: 0; width: 100%; height: 100%; border: 0; display: block; }</style>
</head>
<body>
<iframe src="${src}" title="muvue dashboard"></iframe>
</body>
</html>
`;
}

/** A 403 from a session-gated call means the token is wrong or stale:
 * `serve` mints a new one on every restart. */
export function shouldReprompt(status: number): boolean {
  return status === 403;
}

/** The two mutating human-verb daemon endpoints (src/muvue/api/app.py)
 * this extension's command-bridge commands map onto 1:1 -- no new backend
 * behaviour (plan working rule 3). The third registered command,
 * `muvue.openDashboard`, has no HTTP call of its own; it opens the
 * webview, which itself talks to `GET /`. */
export type MuvueCommandId = "approveNode" | "pauseProject";

export interface EndpointSpec {
  method: "GET" | "POST";
  path: string;
}

/** Resolves a command id + numeric target id to the daemon HTTP request it
 * issues. Kept separate from `vscode.commands.registerCommand` wiring so
 * it's testable without the `vscode` module. */
export function resolveEndpoint(command: MuvueCommandId, id: number): EndpointSpec {
  switch (command) {
    case "approveNode":
      return { method: "POST", path: `/nodes/${id}/approve` };
    case "pauseProject":
      return { method: "POST", path: `/projects/${id}/pause` };
    default: {
      const exhaustive: never = command;
      throw new Error(`unknown command: ${exhaustive}`);
    }
  }
}

/** Mutating calls need the session token printed by `muvue serve` as a
 * bearer header (src/muvue/api/app.py `_require_session`), and every one
 * of them must be `Content-Type: application/json`, body or not. */
export function buildAuthHeaders(token: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

/** Joins a daemon base URL and an endpoint path without producing a
 * double slash, regardless of whether the configured base URL has a
 * trailing slash. */
export function joinUrl(daemonUrl: string, path: string): string {
  return daemonUrl.replace(/\/+$/, "") + path;
}
