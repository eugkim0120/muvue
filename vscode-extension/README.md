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

`serve` prints two lines at startup: a one-time dashboard link
(`#n=<nonce>`) and an `api token: ...` line. The token lives only in the
daemon's memory and changes every time `serve` restarts. Human verbs
(approve, pause, ...) need it.

## Commands

| Command | Title | Daemon call |
|---|---|---|
| `muvue.openDashboard` | muvue: Open Dashboard | `GET /` (via iframe, not a direct fetch) |
| `muvue.approveNode` | muvue: Approve Node | `GET /nodes/{id}`, then `POST /nodes/{id}/approve` |
| `muvue.pauseProject` | muvue: Pause Project (Emergency Stop) | `POST /projects/{id}/pause` |

`approveNode` and `pauseProject` prompt for a numeric id, then for the
api token the first time. The extension keeps the token in memory only,
never in `SecretStorage` or settings (v4 section 8a: nothing
token-shaped on disk), and removes a token that an older version stored.
A 403 means the token is stale (the daemon restarted), so the extension
forgets it and asks again.

`approveNode` reads the node's status first: a node in `review` is
approved as a review, and a `pending` node, or one whose criteria changed
after Gate 2, as a node.

`openDashboard` uses the token to mint a one-time nonce (`POST
/auth/nonce`) and opens the iframe at `#n=<nonce>`. The dashboard
exchanges the nonce. Inside the webview the dashboard is a cross-site
iframe, so its SameSite=Strict cookie is never sent; the page therefore
asks the exchange for the token and keeps it in memory instead. Without
a token the dashboard opens read-only.

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
                  # assertions with plain `node`
xvfb-run -a npm run test:host   # the real Extension Host (drop xvfb-run on a desktop)
```

`test:host` downloads VS Code into `.vscode-test/`, scaffolds a scratch
repository with the muvue CLI (`MUVUE_CMD`, default `muvue`), starts
`muvue serve` on it, and launches VS Code with this extension. Inside the
Extension Host, `test/host/suite.ts` checks, against the live daemon:

- the three commands are registered;
- "Approve Node" approves a node in `review`, and a wrong token gets a
  403 and a second prompt;
- "Open Dashboard" loads the daemon's own `GET /` in the webview, and
  that page exchanges its one-time nonce and opens the SSE stream
  (`/events/stream`). The daemon sits behind a small logging proxy so
  the suite can see those requests;
- "Pause Project" pauses the project.

CI runs both on every push (`.github/workflows/ci.yml`). The api token
goes to the Extension Host through its environment and is never written
to disk.

Two bugs only a real host could show were fixed this way: `main` pointed
at `out/extension.js` while `tsc` writes `out/src/extension.js`, so the
extension never loaded; and with `enableScripts: false` the webview's
sandbox, which the nested iframe inherits, stopped the dashboard's own
JavaScript from running.

Not covered: a person clicking through the command palette. The suite
answers input boxes with a stub.

## Packaging

Not done in P8 (out of scope -- no `.vsix` published, no marketplace
listing). `vsce package` should work once `npm install` has been run, but
has not been exercised here.
