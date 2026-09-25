/**
 * Runs inside the Extension Host started by `./runTest`. Checks the P8
 * acceptance items against a live daemon: the three commands are
 * registered and map to the daemon, and the dashboard webview loads
 * the daemon's own `index.html`, exchanges its nonce and opens the SSE
 * stream. Input boxes are answered by a stub, since no one is typing.
 */

import * as assert from "assert";
import * as vscode from "vscode";

const env = (name: string): string => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set; run this through runTest`);
  return value;
};

async function daemonGet(url: string, token: string): Promise<any> {
  const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  assert.strictEqual(response.status, 200, `GET ${url} -> ${response.status}`);
  return response.json();
}

async function waitForRequests(logUrl: string, wanted: string[], timeoutMs: number): Promise<string[]> {
  const deadline = Date.now() + timeoutMs;
  let seen: string[] = [];
  while (Date.now() < deadline) {
    seen = (await (await fetch(logUrl)).json()) as string[];
    if (wanted.every((w) => seen.includes(w))) return seen;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${JSON.stringify(wanted)}; the daemon saw ${JSON.stringify(seen)}`);
}

export async function run(): Promise<void> {
  const url = env("MUVUE_TEST_URL");
  const token = env("MUVUE_TEST_TOKEN");
  const projectId = env("MUVUE_TEST_PROJECT");
  const nodeId = env("MUVUE_TEST_NODE");

  await vscode.workspace.getConfiguration("muvue").update("daemonUrl", url, vscode.ConfigurationTarget.Global);

  // The first token offered is wrong: the 403 must clear it and ask again.
  const tokens = ["not-the-token", token];
  const ids: string[] = [];
  const errors: string[] = [];
  const window = vscode.window as any;
  window.showInputBox = async (options?: vscode.InputBoxOptions) =>
    options?.password ? tokens.shift() : ids.shift();
  window.showErrorMessage = async (message: string) => {
    errors.push(message);
    return undefined;
  };

  const extension = vscode.extensions.getExtension("muvue.muvue-vscode");
  assert.ok(extension, "extension muvue.muvue-vscode is not installed in the host");
  await extension.activate();

  const commands = await vscode.commands.getCommands(true);
  for (const id of ["muvue.openDashboard", "muvue.approveNode", "muvue.pauseProject"]) {
    assert.ok(commands.includes(id), `${id} is not registered`);
  }

  ids.push(nodeId);
  await vscode.commands.executeCommand("muvue.approveNode");
  assert.deepStrictEqual(errors, []);
  assert.strictEqual(tokens.length, 0, "the 403 did not trigger a second token prompt");
  assert.strictEqual((await daemonGet(`${url}/nodes/${nodeId}`, token)).node.status, "done");

  await vscode.commands.executeCommand("muvue.openDashboard");
  await waitForRequests(`${url}/__log`, ["POST /auth/nonce", "GET /", "POST /auth/exchange", "GET /events/stream"], 30000);

  ids.push(projectId);
  await vscode.commands.executeCommand("muvue.pauseProject");
  assert.deepStrictEqual(errors, []);
  assert.strictEqual((await daemonGet(`${url}/projects/${projectId}`, token)).phase, "paused");
}
