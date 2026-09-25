/**
 * Unit tests for the pure logic in ../src/lib.ts, run with plain `node`
 * (no `vscode` module, no test framework dependency -- see
 * vscode-extension/README.md for why: this environment has no VS Code
 * Extension Host to run `@vscode/test-electron` against).
 *
 * Run via `npm test` (compiles then `node ./out/test/lib.test.js`).
 */

import {
  buildAuthHeaders,
  buildWebviewHtml,
  DEFAULT_DAEMON_URL,
  joinUrl,
  NONCE_PATH,
  approveTarget,
  resolveEndpoint,
  shouldReprompt,
} from "../src/lib";

/**
 * Minimal hand-rolled assertions: this suite runs with plain `node`,
 * outside any test framework and without the `vscode` module.
 */
const assert = {
  strictEqual(actual: unknown, expected: unknown): void {
    if (actual !== expected) {
      throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
  },
  deepStrictEqual(actual: unknown, expected: unknown): void {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) throw new Error(`expected ${e}, got ${a}`);
  },
  match(actual: string, pattern: RegExp): void {
    if (!pattern.test(actual)) throw new Error(`expected ${JSON.stringify(actual)} to match ${pattern}`);
  },
  throws(fn: () => void): void {
    try {
      fn();
    } catch {
      return;
    }
    throw new Error("expected function to throw, it did not");
  },
};

function test(name: string, fn: () => void): void {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    throw err;
  }
}

test("buildWebviewHtml embeds the exact daemon URL as the iframe src", () => {
  const html = buildWebviewHtml("http://127.0.0.1:8765");
  assert.match(html, /<iframe src="http:\/\/127\.0\.0\.1:8765" title="muvue dashboard"><\/iframe>/);
});

test("buildWebviewHtml hands the dashboard a one-time nonce in the fragment", () => {
  const html = buildWebviewHtml("http://127.0.0.1:8765/", "abc-_123");
  assert.match(html, /<iframe src="http:\/\/127\.0\.0\.1:8765\/#n=abc-_123" title="muvue dashboard"><\/iframe>/);
});

test("buildWebviewHtml escapes a nonce it didn't mint", () => {
  const html = buildWebviewHtml("http://127.0.0.1:8765", "a\"><script>");
  assert.match(html, /#n=a%22%3E%3Cscript%3E"/);
});

test("NONCE_PATH is the daemon's nonce-minting endpoint", () => {
  assert.strictEqual(NONCE_PATH, "/auth/nonce");
});

test("shouldReprompt only on 403 (a stale token after `serve` restarted)", () => {
  assert.strictEqual(shouldReprompt(403), true);
  assert.strictEqual(shouldReprompt(409), false);
  assert.strictEqual(shouldReprompt(200), false);
});

test("buildWebviewHtml scopes the CSP frame-src to the daemon's origin", () => {
  const html = buildWebviewHtml("http://127.0.0.1:9999/");
  assert.match(html, /frame-src http:\/\/127\.0\.0\.1:9999;/);
  // No script runs in the outer wrapper page: default-src 'none' with no
  // script-src override means scripts are blocked by default.
  assert.match(html, /default-src 'none'/);
});

test("buildWebviewHtml rejects a malformed daemon URL (fails loud, not silent)", () => {
  assert.throws(() => buildWebviewHtml("not-a-url"));
});

test("resolveEndpoint maps approveNode onto POST /nodes/{id}/approve", () => {
  assert.deepStrictEqual(resolveEndpoint("approveNode", 42), {
    method: "POST",
    path: "/nodes/42/approve",
  });
});

test("approveTarget approves a node in review as a review", () => {
  assert.strictEqual(approveTarget("review"), "review");
});

test("approveTarget approves a pending or re-gated node as a node", () => {
  assert.strictEqual(approveTarget("pending"), "node");
  assert.strictEqual(approveTarget("awaiting_approval"), "node");
});

test("resolveEndpoint maps pauseProject onto POST /projects/{id}/pause", () => {
  assert.deepStrictEqual(resolveEndpoint("pauseProject", 7), {
    method: "POST",
    path: "/projects/7/pause",
  });
});

test("buildAuthHeaders sets a Bearer header when a token is given", () => {
  assert.deepStrictEqual(buildAuthHeaders("tok123"), {
    "Content-Type": "application/json",
    Authorization: "Bearer tok123",
  });
});

test("buildAuthHeaders omits Authorization when there is no token", () => {
  assert.deepStrictEqual(buildAuthHeaders(undefined), { "Content-Type": "application/json" });
});

test("joinUrl avoids a double slash regardless of trailing slash on the base", () => {
  assert.strictEqual(joinUrl("http://127.0.0.1:8765/", "/nodes/1/approve"), "http://127.0.0.1:8765/nodes/1/approve");
  assert.strictEqual(joinUrl("http://127.0.0.1:8765", "/nodes/1/approve"), "http://127.0.0.1:8765/nodes/1/approve");
});

test("DEFAULT_DAEMON_URL matches muvue serve's default host/port", () => {
  assert.strictEqual(DEFAULT_DAEMON_URL, "http://127.0.0.1:8765");
});

console.log("all lib.ts tests passed");
