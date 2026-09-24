# muvue (VS Code extension)

Thin webview + command bridge for the muvue dashboard daemon (plan section
11, phase P8). This extension does not reimplement any part of the
dashboard: its webview is a single `<iframe>` pointed at a running `muvue
serve` daemon's real HTTP URL, so it always renders whatever `index.html`
that daemon currently serves (P2, extended by P7) -- never a bundled copy.

## Scope

- **Does:** open a webview panel that iframes the daemon's dashboard;
  register three commands that call the daemon's existing HTTP API.
- **Does not:** start or manage the `muvue serve` process (see
  `docs/decisions.md` #66); reimplement any dashboard UI, SSE handling, or
  business logic (all of that already lives in `index.html` and
  `src/muvue/core`, and runs unchanged inside the iframe); add new backend
  endpoints.

## Prerequisites

A `muvue serve` daemon must already be running for the repo you want to
look at:

```sh
muvue serve /path/to/repo --port 8765
```

`serve` prints a session token once at startup (also written to
`<repo>/.muvue/session`, `0600`). Human verbs (approve, pause, ...) need
that token.

## Commands

| Command | Title | Daemon call |
|---|---|---|
| `muvue.openDashboard` | muvue: Open Dashboard | `GET /` (via iframe, not a direct fetch) |
| `muvue.approveNode` | muvue: Approve Node | `POST /nodes/{id}/approve` |
| `muvue.pauseProject` | muvue: Pause Project (Emergency Stop) | `POST /projects/{id}/pause` |

`approveNode` and `pauseProject` prompt for a numeric id, then for the
session token the first time (cached afterwards in VS Code's
`SecretStorage` for this machine).

## Configuration

- `muvue.daemonUrl` (default `http://127.0.0.1:8765`): base URL of the
  running daemon.

## Build

```sh
npm install
npm run compile   # tsc -p ./  -> out/
```

## Test

```sh
npm test          # compiles, then runs test/lib.test.ts's pure-logic
                   # assertions with plain `node` (no framework, no
                   # `vscode` module needed)
```

The Python side of this repo also has a real-process integration test,
`tests/test_vscode_extension_p8.py`, which spawns a real `muvue serve`
subprocess and asserts (1) `GET /` is byte-identical to the shipped
`src/muvue/api/static/index.html` -- exactly what this extension's iframe
would load -- and (2) `GET /events/stream` is a live SSE endpoint that
actually emits a `data:` line, which is what that same `index.html`'s own
`EventSource` connects to once the page is loaded inside the iframe.

## What is verified, and what is not

This environment has no live VS Code Extension Host (no `code` binary, no
`@vscode/test-electron` sandbox available). What's actually been
exercised:

- **Type-checked and compiled cleanly** against `@types/vscode` (`tsc -p
  ./`, strict mode) -- `src/extension.ts` and `src/lib.ts`.
- **Unit-tested for real** (`test/lib.test.ts`, plain `node`): webview
  HTML generation (iframe src, CSP scoping), the three commands' endpoint
  mapping, auth header construction, and URL joining.
- **Integration-tested for real against a live daemon**
  (`tests/test_vscode_extension_p8.py`, Python/pytest, spawns an actual
  `muvue serve` subprocess): the exact URLs the extension points its
  webview and would call, respond exactly as this extension assumes.
- **Not run inside an actual VS Code window.** `vscode.window.
  createWebviewPanel`, the CSP as VS Code's real webview host enforces it,
  `SecretStorage`, and the command palette wiring have not been exercised
  against a real Extension Host. If you have VS Code installed, the
  fastest manual check is: `code --extensionDevelopmentPath=$(pwd)
  <some-repo>`, then run "muvue: Open Dashboard" from the command palette
  against a `muvue serve` you started separately.

## Packaging

Not done in P8 (out of scope -- no `.vsix` published, no marketplace
listing). `vsce package` should work once `npm install` has been run, but
has not been exercised here.
