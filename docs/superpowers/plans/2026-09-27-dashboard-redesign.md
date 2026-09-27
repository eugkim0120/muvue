# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-written dashboard with a Preact/TypeScript app, organised around the workflow, styled like a native Apple app in the Claude palette, built into one self-contained `index.html` that the daemon serves unchanged.

**Architecture:** Source in `dashboard/`, built by Vite with `vite-plugin-singlefile` into `src/muvue/api/static/index.html` (committed). Hash router, `@preact/signals` for shared state, one `routes.ts` module that owns every API path, one `client.ts` that owns `fetch`. Pages: Plan, Spec, Inbox, Activity, Spend; a task sheet opens over any page via `?node=`.

**Tech Stack:** Preact 10, `@preact/signals`, TypeScript 5, Vite, `vite-plugin-singlefile`, Vitest + `@testing-library/preact` + jsdom, Node 22. Backend: FastAPI (one new route), pytest.

**Spec:** `docs/superpowers/specs/2026-09-27-dashboard-redesign-design.md` — read it first; every task below argues from it.

## Global Constraints

- Runtime: the built `src/muvue/api/static/index.html` loads nothing from the network except the daemon's own API. No `<script src=`, no `<link href="https://`, no web fonts.
- The strings `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie` never appear in the built file. The session token lives only in a module variable.
- No inline event handler attributes in markup.
- `fetch(` only in `dashboard/src/api/client.ts` and `dashboard/src/api/auth.ts`. Every API path literal only in `dashboard/src/api/routes.ts`.
- Runtime npm deps: `preact`, `@preact/signals` only. `build.minify = false`.
- Never `alert()`, `confirm()` or `prompt()`. Toasts and sheets instead.
- All action buttons hidden (not disabled) while `authed` is false.
- Actions row renders before Overview/Diff/Logs in the task sheet (the phone bug fixed by a524cad must not return).
- Font stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, "Segoe UI", Roboto, sans-serif`. Touch targets at least 44 px on phone. Bottom UI pads by `env(safe-area-inset-bottom)`.
- Colour tokens exactly as the spec's table. Status colours: pending `#8A8986`, ready `#5B8DEF`, in_progress `#D9A33B`, review `#8C6FD6`, awaiting_approval `#C46FA8`, done `#4C9A6A`, blocked `#C94F4F`, failed `#9C2F2F`.
- Commit messages in normal prose. Trailer paragraph on every commit:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
  ```
- Working directory for Python: `/home/eugene/projects/muvue`, commands via `uv run ...`. For node: `cd /home/eugene/projects/muvue/dashboard`. Use `cd` inside every command; the shell cwd does not persist.
- Do not touch `vscode-extension/`. Do not touch the daemon's security middleware.
- After Task 15, run `uv run pytest -q` (all green) before the final commit.

---

### Task 1: Scaffold `dashboard/` and the build that writes `static/index.html`

**Files:**
- Create: `dashboard/package.json`, `dashboard/tsconfig.json`, `dashboard/vite.config.ts`, `dashboard/index.html`, `dashboard/src/main.tsx`, `dashboard/src/app.tsx`, `dashboard/test/setup.ts`, `dashboard/test/smoke.test.tsx`, `dashboard/.gitignore`
- Modify: `.gitignore` (root)

**Interfaces:**
- Produces: `npm run build` in `dashboard/` writes `dashboard/dist/index.html` and copies it to `src/muvue/api/static/index.html`. `npm test`, `npm run typecheck`, `npm run dev` (watch build).

- [ ] **Step 1: Write package.json**

```json
{
  "name": "muvue-dashboard",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite build --watch",
    "build": "vite build",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@preact/signals": "^1.3.0",
    "preact": "^10.24.0"
  },
  "devDependencies": {
    "@preact/preset-vite": "^2.9.0",
    "@testing-library/preact": "^3.2.4",
    "jsdom": "^25.0.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0",
    "vite-plugin-singlefile": "^2.0.0",
    "vitest": "^2.1.0"
  }
}
```

Run: `cd /home/eugene/projects/muvue/dashboard && npm install`. If a caret range fails to resolve, take the latest major npm offers and keep the caret. Commit `package-lock.json`.

- [ ] **Step 2: Write tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "jsxImportSource": "preact",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "skipLibCheck": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "types": ["vitest/globals"],
    "noEmit": true
  },
  "include": ["src", "test", "vite.config.ts"]
}
```

- [ ] **Step 3: Write vite.config.ts**

```ts
import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const here = fileURLToPath(new URL(".", import.meta.url));
// The daemon serves this file on every GET /, so a build (or a watch
// rebuild) is live as soon as the copy lands.
const STATIC_INDEX = resolve(here, "../src/muvue/api/static/index.html");

function copyToStatic() {
  return {
    name: "muvue-copy-to-static",
    closeBundle() {
      copyFileSync(resolve(here, "dist/index.html"), STATIC_INDEX);
    },
  };
}

export default defineConfig({
  plugins: [preact(), viteSingleFile(), copyToStatic()],
  build: { minify: false, target: "es2020", outDir: "dist", emptyOutDir: true, cssCodeSplit: false },
  test: { environment: "jsdom", globals: true, setupFiles: ["./test/setup.ts"], include: ["test/**/*.test.{ts,tsx}"] },
});
```

- [ ] **Step 4: Write index.html, main.tsx, app.tsx, test setup, .gitignore**

`dashboard/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>muvue</title>
</head>
<body>
<div id="root"></div>
<script type="module" src="/src/main.tsx"></script>
</body>
</html>
```

`dashboard/src/main.tsx`:
```tsx
import { render } from "preact";
import { App } from "./app";

render(<App />, document.getElementById("root")!);
```

`dashboard/src/app.tsx`:
```tsx
export function App() {
  return <h1>muvue</h1>;
}
```

`dashboard/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```
If that import fails, add `@testing-library/jest-dom` to devDependencies (`npm install -D @testing-library/jest-dom`).

`dashboard/.gitignore`:
```
node_modules/
dist/
```

Root `.gitignore`: nothing to add (the dashboard's own file covers it).

- [ ] **Step 5: Write the smoke test**

`dashboard/test/smoke.test.tsx`:
```tsx
import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";

test("renders the app title", () => {
  render(<App />);
  expect(screen.getByText("muvue")).toBeInTheDocument();
});
```

- [ ] **Step 6: Run test, typecheck, build**

Run: `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck && npm run build`
Expected: 1 test passed; no type errors; `dist/index.html` written; `git -C .. status --short src/muvue/api/static/index.html` shows ` M`.

Check the built file: `grep -c '<script[^>]*src=' /home/eugene/projects/muvue/src/muvue/api/static/index.html` prints `0`, and `grep -c 'type="module"' ...` prints `1`.

- [ ] **Step 7: Restore the old dashboard for now**

The old page must keep working until Task 15 swaps it. Run: `cd /home/eugene/projects/muvue && git checkout -- src/muvue/api/static/index.html`.

- [ ] **Step 8: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Scaffold the Preact dashboard build

A Vite build in dashboard/ writes one self-contained index.html and
copies it over the daemon's static page. The old page is untouched
until the new one is complete.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 2: `GET /agents` on the daemon

**Files:**
- Modify: `src/muvue/api/app.py` (after the `@app.get("/healthz")` route, about line 300)
- Modify: `docs/protocol.md` (the read-only route list, about line 338)
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `GET /agents` → `{"agents": [<sorted names of config.agents>]}`, no session needed.

- [ ] **Step 1: Write the failing test** (append to `tests/test_api.py`)

```python
def test_agents_lists_configured_names_without_a_session(repo):
    from muvue.core.config import AgentConfig

    config = MuvueConfig(
        checks=ChecksConfig(test="true", lint="true"),
        agents={"zed": AgentConfig(command="zed", cost_model="usd"), "claude": AgentConfig(command="claude", cost_model="usd")},
    )
    c = TestClient(create_app(repo, config=config), base_url=BASE_URL)
    r = c.get("/agents")
    assert r.status_code == 200
    assert r.json() == {"agents": ["claude", "zed"]}
```

- [ ] **Step 2: Run it**

Run: `cd /home/eugene/projects/muvue && uv run pytest -q tests/test_api.py -k agents_lists`
Expected: FAIL, status 404.

- [ ] **Step 3: Add the route** after `healthz` in `src/muvue/api/app.py`:

```python
    @app.get("/agents")
    def list_agents() -> dict:
        """Names under `[agents.*]`, for the dashboard's start-with-agent
        picker. Names only: a command line may embed local paths."""
        return {"agents": sorted(config.agents)}
```

- [ ] **Step 4: Run the test**

Run: `cd /home/eugene/projects/muvue && uv run pytest -q tests/test_api.py -k agents_lists`
Expected: PASS.

- [ ] **Step 5: Document** — in `docs/protocol.md`, in the read-only route list, after `GET /status`, add `` `GET /agents` (names under `[agents.*]`) ``.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add src/muvue/api/app.py tests/test_api.py docs/protocol.md && git commit -m "Add GET /agents for the dashboard's agent picker

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 3: Routes, API client, auth

**Files:**
- Create: `dashboard/src/api/routes.ts`, `dashboard/src/api/client.ts`, `dashboard/src/api/auth.ts`
- Test: `dashboard/test/client.test.ts`, `dashboard/test/no-stray-fetch.test.ts`

**Interfaces:**
- Produces: `routes.<name>(...)` path builders; `api<T>(path, init?)`, `post<T>(path, body?)`, `setToken(t)`, `onForbidden(cb)`, `ApiError`; `exchangeFragmentNonce(): Promise<boolean>`, `checkAuth(): Promise<boolean>`.

- [ ] **Step 1: Write routes.ts**

```ts
// The only module that spells an API path. tests/test_dashboard_static.py
// reads this file and checks every path against the daemon's routes.
function q(path: string, params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) search.set(k, String(v));
  const s = search.toString();
  return s ? `${path}?${s}` : path;
}

export const routes = {
  healthz: () => "/healthz",
  authCheck: () => "/auth/check",
  authExchange: () => "/auth/exchange",
  eventsStream: () => "/events/stream",
  projects: () => "/projects",
  agents: () => "/agents",
  inbox: () => "/inbox",
  kpis: () => "/kpis",
  graph: (projectId: number | null) => q("/graph", { project_id: projectId }),
  nodes: (projectId: number | null) => q("/nodes", { project_id: projectId }),
  events: (projectId: number | null, limit = 100) => q("/events", { limit, project_id: projectId }),
  node: (id: number) => `/nodes/${id}`,
  nodeDiff: (id: number) => `/nodes/${id}/diff`,
  nodeLogs: (id: number, lines = 200) => q(`/nodes/${id}/logs`, { lines }),
  nodeApprove: (id: number) => `/nodes/${id}/approve`,
  nodeReject: (id: number) => `/nodes/${id}/reject`,
  nodeStart: (id: number, agent: string) => q(`/nodes/${id}/start`, { agent }),
  nodeComment: (id: number) => `/nodes/${id}/comment`,
  questionAnswer: (id: number) => `/questions/${id}/answer`,
  eventAck: (id: number) => `/events/${id}/ack`,
  revisions: (projectId: number) => `/projects/${projectId}/revisions`,
  projectPause: (projectId: number) => `/projects/${projectId}/pause`,
  projectResume: (projectId: number) => `/projects/${projectId}/resume`,
  closePreview: (projectId: number) => `/projects/${projectId}/close-preview`,
  projectClose: (projectId: number) => `/projects/${projectId}/close`,
};
```

- [ ] **Step 2: Write client.ts**

```ts
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Inside the VS Code webview the session cookie is never sent, so the
// nonce exchange hands back the token and it is kept here, in memory.
let token = "";
let forbidden: () => void = () => {};

export function setToken(t: string): void { token = t; }
export function onForbidden(handler: () => void): void { forbidden = handler; }

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) };
  if (token) headers["Authorization"] = "Bearer " + token;
  const r = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const type = r.headers.get("content-type") || "";
  const body: unknown = type.startsWith("application/json") ? await r.json() : await r.text();
  if (r.status === 403 && init.method === "POST") forbidden();
  if (!r.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body ? String((body as { detail: unknown }).detail) : "";
    throw new ApiError(detail || r.statusText || `HTTP ${r.status}`, r.status);
  }
  return body as T;
}

export function post<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
```

- [ ] **Step 3: Write auth.ts**

```ts
import { routes } from "./routes";
import { api, ApiError, setToken } from "./client";

export async function checkAuth(): Promise<boolean> {
  try {
    await api(routes.authCheck());
    return true;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return false;
    throw e;
  }
}

// `muvue serve` prints a link whose `#n=` fragment is a single-use
// nonce. It is exchanged once for an HttpOnly cookie; in an iframe the
// exchange also returns the token for the Authorization header.
export async function exchangeFragmentNonce(): Promise<boolean> {
  const match = (window.location.hash || "").match(/(?:^#|[&#])n=([^&]+)/);
  if (!match) return checkAuth();
  const embedded = window.top !== window.self;
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const r = await fetch(routes.authExchange(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ nonce: decodeURIComponent(match[1]!), header: embedded }),
  });
  if (r.ok) {
    const body = (await r.json()) as { token?: string };
    if (body.token) setToken(body.token);
  }
  // A used or expired nonce is not an error: the page is read-only.
  return checkAuth();
}
```

- [ ] **Step 4: Write client.test.ts**

```ts
import { api, post, setToken, onForbidden, ApiError } from "../src/api/client";

function mockFetch(status: number, body: unknown, json = true) {
  const fn = vi.fn(async () => ({
    ok: status < 400,
    status,
    statusText: "status " + status,
    headers: new Headers({ "content-type": json ? "application/json" : "text/plain" }),
    json: async () => body,
    text: async () => String(body),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => { vi.unstubAllGlobals(); setToken(""); });

test("post sends JSON with the content type and same-origin credentials", async () => {
  const fetchMock = mockFetch(200, { ok: 1 });
  await post("/x", { a: 1 });
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(init.method).toBe("POST");
  expect(init.body).toBe('{"a":1}');
  expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  expect(init.credentials).toBe("same-origin");
});

test("a token becomes a bearer header", async () => {
  const fetchMock = mockFetch(200, {});
  setToken("abc");
  await api("/x");
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer abc");
});

test("detail is the error message and 403 on POST calls the forbidden hook", async () => {
  mockFetch(403, { detail: "no session" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ message: "no session", status: 403 });
  expect(hook).toHaveBeenCalledTimes(1);
});

test("403 on GET does not call the forbidden hook", async () => {
  mockFetch(403, { detail: "no" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
  expect(hook).not.toHaveBeenCalled();
});

test("plain text bodies are returned as text", async () => {
  mockFetch(200, "line1\nline2", false);
  expect(await api<string>("/logs")).toBe("line1\nline2");
});
```

- [ ] **Step 5: Write no-stray-fetch.test.ts**

```ts
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..", "src");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const files = walk(SRC).filter((f) => /\.(ts|tsx)$/.test(f));

test("fetch is called only from the api client and auth modules", () => {
  const offenders = files.filter((f) => /\bfetch\(/.test(readFileSync(f, "utf8")) && !/api\/(client|auth)\.ts$/.test(f));
  expect(offenders).toEqual([]);
});

test("API path literals live only in routes.ts", () => {
  const offenders: string[] = [];
  for (const f of files) {
    if (/api\/routes\.ts$/.test(f)) continue;
    const text = readFileSync(f, "utf8");
    // A string or template starting with "/" followed by a word is an API path.
    if (/["`]\/(nodes|projects|events|questions|inbox|kpis|graph|auth|healthz|agents|status|brief)\b/.test(text)) offenders.push(f);
  }
  expect(offenders).toEqual([]);
});
```

- [ ] **Step 6: Run**

Run: `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`
Expected: all pass (`__dirname` is available under Vitest; if typecheck complains, replace it with `fileURLToPath(new URL(".", import.meta.url))`).

- [ ] **Step 7: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: API routes, fetch client and nonce exchange

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 4: Shared state, router, SSE stream

**Files:**
- Create: `dashboard/src/state.ts`, `dashboard/src/router.ts`, `dashboard/src/api/stream.ts`
- Test: `dashboard/test/router.test.ts`

**Interfaces:**
- Produces from `state.ts`: signals `projects`, `projectId`, `currentProject` (computed), `authed`, `inboxCount`, `refreshTick`, `protocolVersion`, `toasts`; functions `refresh()`, `toast(text, kind?)`, `toastError(e)`, `loadProjects()`, `setAuthed(ok)`. Types `Project`, `NodeRow`, `NodeStatus`.
- Produces from `router.ts`: `route` signal `{page: string, params: string[], query: URLSearchParams}`, `parseHash(hash)`, `navigate(hash)`, `openNode(id)`, `closeNode()`, `resetRouteFromLocation()`.
- Produces from `stream.ts`: `connectStream()`.

- [ ] **Step 1: Write state.ts**

```ts
import { computed, signal } from "@preact/signals";
import { api } from "./api/client";
import { routes } from "./api/routes";

export type Phase = "planning" | "executing" | "paused" | "closed";
export type Project = { id: number; goal: string; phase: Phase };
export type NodeStatus = "pending" | "ready" | "in_progress" | "review" | "awaiting_approval" | "done" | "blocked" | "failed";
export type RiskTier = "low" | "medium" | "high";
export type NodeRow = {
  id: number; project_id: number; parent_id: number | null; kind: string; title: string;
  status: NodeStatus; risk_tier: RiskTier; owner: string | null; block_reason?: string | null;
};

export const projects = signal<Project[]>([]);
export const projectId = signal<number | null>(null);
export const currentProject = computed(() => projects.value.find((p) => p.id === projectId.value) ?? null);
export const authed = signal(false);
export const inboxCount = signal(0);
export const refreshTick = signal(0);
export const protocolVersion = signal<number | null>(null);

export function refresh(): void { refreshTick.value = refreshTick.value + 1; }
export function setAuthed(ok: boolean): void { authed.value = ok; }

export type Toast = { id: number; text: string; kind: "info" | "error" };
export const toasts = signal<Toast[]>([]);
let toastSeq = 0;
export function toast(text: string, kind: Toast["kind"] = "info"): void {
  const id = ++toastSeq;
  toasts.value = [...toasts.value, { id, text, kind }];
  setTimeout(() => { toasts.value = toasts.value.filter((t) => t.id !== id); }, 4000);
}
export function dismissToast(id: number): void { toasts.value = toasts.value.filter((t) => t.id !== id); }
export function toastError(e: unknown): void { toast(e instanceof Error ? e.message : String(e), "error"); }

// Picks the newest open project the first time, or when the chosen one
// disappears; otherwise keeps the user's choice across refreshes.
export async function loadProjects(): Promise<void> {
  const list = await api<Project[]>(routes.projects());
  projects.value = list;
  const chosen = list.find((p) => p.id === projectId.value);
  if (!chosen) {
    const open = list.filter((p) => p.phase !== "closed");
    projectId.value = list.length ? (open.length ? open[open.length - 1]! : list[list.length - 1]!).id : null;
  }
}
```

- [ ] **Step 2: Write router.test.ts**

```ts
import { parseHash, navigate, openNode, closeNode, route, resetRouteFromLocation } from "../src/router";

test("empty hash is the plan page", () => {
  expect(parseHash("")).toMatchObject({ page: "plan", params: [] });
  expect(parseHash("#/")).toMatchObject({ page: "plan", params: [] });
});

test("path and query parse", () => {
  const r = parseHash("#/spec/3?node=7");
  expect(r.page).toBe("spec");
  expect(r.params).toEqual(["3"]);
  expect(r.query.get("node")).toBe("7");
});

test("the nonce fragment is never a route", () => {
  expect(parseHash("#n=abc").page).toBe("plan");
});

test("openNode keeps the page and closeNode drops the query", () => {
  window.location.hash = "#/inbox";
  resetRouteFromLocation();
  openNode(7);
  expect(window.location.hash).toBe("#/inbox?node=7");
  closeNode();
  expect(window.location.hash).toBe("#/inbox");
});

test("navigate updates the route signal", async () => {
  navigate("#/spend");
  await new Promise((r) => setTimeout(r, 0));
  resetRouteFromLocation();
  expect(route.value.page).toBe("spend");
});
```

- [ ] **Step 3: Run it** — `cd /home/eugene/projects/muvue/dashboard && npm test -- router`. Expected: FAIL, module not found.

- [ ] **Step 4: Write router.ts**

```ts
import { signal } from "@preact/signals";

export type Route = { page: string; params: string[]; query: URLSearchParams };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart = "", queryPart = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  // "#n=<nonce>" is the one-time login link, consumed before the router runs.
  if (!parts.length || parts[0]!.startsWith("n=")) return { page: "plan", params: [], query: new URLSearchParams(queryPart) };
  return { page: parts[0]!, params: parts.slice(1), query: new URLSearchParams(queryPart) };
}

export const route = signal<Route>(parseHash(typeof window === "undefined" ? "" : window.location.hash));

export function resetRouteFromLocation(): void { route.value = parseHash(window.location.hash); }

if (typeof window !== "undefined") window.addEventListener("hashchange", resetRouteFromLocation);

export function navigate(hash: string): void { window.location.hash = hash; }

function currentPath(): string {
  const r = parseHash(window.location.hash);
  return "#/" + [r.page, ...r.params].join("/");
}

export function openNode(id: number): void {
  const r = parseHash(window.location.hash);
  r.query.set("node", String(id));
  navigate(currentPath() + "?" + r.query.toString());
}

export function closeNode(): void {
  const r = parseHash(window.location.hash);
  r.query.delete("node");
  const q = r.query.toString();
  navigate(currentPath() + (q ? "?" + q : ""));
}
```

- [ ] **Step 5: Write stream.ts**

```ts
import { routes } from "./routes";
import { refresh } from "../state";

// The daemon sends one SSE message per DB change. Pages refetch on the
// refresh tick; an open sheet refetches its own node instead of closing.
export function connectStream(): void {
  if (typeof EventSource === "undefined") return; // manual refresh still works
  const src = new EventSource(routes.eventsStream());
  src.onmessage = () => refresh();
}
```

- [ ] **Step 6: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 7: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: shared state signals, hash router and SSE refresh

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 5: Design tokens and base styles

**Files:**
- Create: `dashboard/src/styles/tokens.css`, `dashboard/src/styles/base.css`
- Modify: `dashboard/src/main.tsx` (import both)

**Interfaces:**
- Produces CSS custom properties and utility classes used by every later task: `--bg --surface --surface-2 --border --text --text-2 --accent --accent-hover --on-accent --danger --radius --radius-lg --shadow --font --mono`, status colours `--st-<status>`, classes `.card .row .muted .stack .grid-kpi .mono .callout .empty .visually-hidden`.

- [ ] **Step 1: Write tokens.css**

```css
:root {
  color-scheme: light dark;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, monospace;
  --radius: 10px;
  --radius-lg: 14px;
  --bg: #FAF9F5;
  --surface: #FFFFFF;
  --surface-2: #F0EEE6;
  --border: #E5E2D9;
  --text: #1F1E1D;
  --text-2: #6B6A66;
  --accent: #D97757;
  --accent-hover: #C4623F;
  --on-accent: #FFFFFF;
  --danger: #C94F4F;
  --shadow: 0 1px 2px rgb(0 0 0 / .06), 0 4px 12px rgb(0 0 0 / .06);
  --st-pending: #8A8986;
  --st-ready: #5B8DEF;
  --st-in_progress: #D9A33B;
  --st-review: #8C6FD6;
  --st-awaiting_approval: #C46FA8;
  --st-done: #4C9A6A;
  --st-blocked: #C94F4F;
  --st-failed: #9C2F2F;
  --tier-medium: #D9A33B;
  --tab-h: 56px;
  --sidebar-w: 240px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1F1E1D;
    --surface: #2B2A27;
    --surface-2: #353431;
    --border: #3E3D39;
    --text: #F4F3EE;
    --text-2: #A8A69F;
    --accent-hover: #E58A6C;
    --on-accent: #1F1E1D;
    --danger: #E06B6B;
    --shadow: 0 1px 2px rgb(0 0 0 / .4);
  }
}
```

- [ ] **Step 2: Write base.css**

```css
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { font-family: var(--font); font-size: 15px; line-height: 1.4; color: var(--text); background: var(--bg); -webkit-font-smoothing: antialiased; }
h1, h2, h3 { margin: 0; font-weight: 600; }
h1 { font-size: 22px; } h2 { font-size: 17px; } h3 { font-size: 15px; }
a { color: var(--accent); }
button { font: inherit; color: inherit; }
input, textarea, select { font: inherit; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; width: 100%; min-height: 44px; }
textarea { min-height: 88px; resize: vertical; }
pre { font-family: var(--mono); font-size: 12px; line-height: 1.5; margin: 0; white-space: pre-wrap; word-break: break-word; background: var(--surface-2); border-radius: var(--radius); padding: 10px 12px; }
pre.scroll-x { white-space: pre; overflow-x: auto; }
.mono { font-family: var(--mono); }
.muted { color: var(--text-2); font-size: 13px; }
.caption { color: var(--text-2); font-size: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 14px 16px; box-shadow: var(--shadow); }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.row.between { justify-content: space-between; }
.stack { display: flex; flex-direction: column; gap: 12px; }
.stack.tight { gap: 6px; }
.grid-kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.callout { border-left: 3px solid var(--accent); background: var(--surface-2); padding: 8px 12px; border-radius: 0 var(--radius) var(--radius) 0; font-size: 13px; }
.callout.danger { border-left-color: var(--danger); }
.empty { text-align: center; color: var(--text-2); padding: 48px 16px; }
.empty svg { width: 48px; height: 48px; margin-bottom: 12px; color: var(--st-done); }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.page { padding: 16px; max-width: 1100px; margin: 0 auto; width: 100%; }
.page-title { margin-bottom: 12px; }
.list { display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }
.list-row { display: flex; align-items: center; gap: 12px; padding: 12px 16px; min-height: 52px; border-top: 1px solid var(--border); background: none; border-left: 0; border-right: 0; border-bottom: 0; width: 100%; text-align: left; cursor: pointer; }
.list-row:first-child { border-top: 0; }
.list-row:hover { background: var(--surface-2); }
.list-row .grow { flex: 1; min-width: 0; }
.list-row .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--st-pending); }
.chev { color: var(--text-2); flex: none; }
.tier-low { color: var(--text-2); } .tier-medium { color: var(--tier-medium); } .tier-high { color: var(--danger); }
.progress { height: 8px; border-radius: 4px; background: var(--surface-2); overflow: hidden; }
.progress > span { display: block; height: 100%; background: var(--accent); }
.progress.warn > span { background: var(--tier-medium); }
.progress.exhausted > span { background: var(--danger); }
.d-add { color: var(--st-done); } .d-del { color: var(--danger); } .d-hunk { color: var(--st-ready); } .d-meta { color: var(--text-2); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
```

- [ ] **Step 3: Import** in `main.tsx`, above the `preact` import:

```ts
import "./styles/tokens.css";
import "./styles/base.css";
```

- [ ] **Step 4: Build and check the CSS is inlined**

Run: `cd /home/eugene/projects/muvue/dashboard && npm run build && grep -c -- '--accent: #D97757' dist/index.html && cd .. && git checkout -- src/muvue/api/static/index.html`
Expected: `1`.

- [ ] **Step 5: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: Claude palette tokens and base styles

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 6: UI kit

**Files:**
- Create: `dashboard/src/ui/Button.tsx`, `Pill.tsx`, `Segmented.tsx`, `Sheet.tsx`, `Toasts.tsx`, `Confirm.tsx`, `Empty.tsx`, `Icon.tsx`, `NodeRowItem.tsx`, `dashboard/src/ui/ui.css`
- Modify: `dashboard/src/main.tsx` (import `./ui/ui.css`)
- Test: `dashboard/test/ui.test.tsx`

**Interfaces:**
- `Button({variant?: "filled"|"outline"|"danger"|"plain", onClick, disabled?, children, type?})`
- `Pill({status: NodeStatus | Phase})`, `Tier({tier: RiskTier})`
- `Segmented({options: {value: string, label: string}[], value, onChange})`
- `Sheet({title, onClose, children})` — right sheet at ≥ 900 px, bottom sheet below.
- `Toasts()` — renders `toasts` signal.
- `Confirm({title, body, confirmLabel, danger?, onConfirm, onCancel})` — a `Sheet` with two buttons.
- `Empty({text})`, `Icon({name: "plan"|"inbox"|"activity"|"spend"|"search"|"close"|"chevron"|"check"|"more"})`
- `NodeRowItem({node: NodeRow, right?: ComponentChildren})` — a `.list-row` button that calls `openNode(node.id)`.

- [ ] **Step 1: Write the components**

`Button.tsx`:
```tsx
import type { ComponentChildren } from "preact";

type Props = {
  variant?: "filled" | "outline" | "danger" | "plain";
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  children: ComponentChildren;
};

export function Button({ variant = "outline", onClick, disabled, type = "button", children }: Props) {
  return (
    <button type={type} class={"btn btn-" + variant} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}
```

`Pill.tsx`:
```tsx
import type { NodeStatus, Phase, RiskTier } from "../state";

export function Pill({ status }: { status: NodeStatus | Phase }) {
  return <span class={"pill st-" + status}>{status.replace(/_/g, " ")}</span>;
}

export function Tier({ tier }: { tier: RiskTier }) {
  return <span class={"tier tier-" + tier}>{tier} risk</span>;
}
```

`Segmented.tsx`:
```tsx
type Option = { value: string; label: string };

export function Segmented({ options, value, onChange }: { options: Option[]; value: string; onChange: (v: string) => void }) {
  return (
    <div class="segmented" role="tablist">
      {options.map((o) => (
        <button type="button" role="tab" aria-selected={o.value === value} class={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
```

`Sheet.tsx`:
```tsx
import type { ComponentChildren } from "preact";
import { useEffect } from "preact/hooks";
import { Icon } from "./Icon";

export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ComponentChildren }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div class="sheet-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div class="sheet-handle" aria-hidden="true" />
        <div class="sheet-head">
          <h2>{title}</h2>
          <button type="button" class="icon-btn" aria-label="close" onClick={onClose}><Icon name="close" /></button>
        </div>
        <div class="sheet-body">{children}</div>
      </div>
    </div>
  );
}
```

`Toasts.tsx`:
```tsx
import { toasts, dismissToast } from "../state";

export function Toasts() {
  return (
    <div class="toasts" aria-live="polite">
      {toasts.value.map((t) => (
        <button type="button" key={t.id} class={"toast " + t.kind} onClick={() => dismissToast(t.id)}>{t.text}</button>
      ))}
    </div>
  );
}
```

`Confirm.tsx`:
```tsx
import type { ComponentChildren } from "preact";
import { Sheet } from "./Sheet";
import { Button } from "./Button";

type Props = { title: string; body: ComponentChildren; confirmLabel: string; danger?: boolean; onConfirm: () => void; onCancel: () => void };

export function Confirm({ title, body, confirmLabel, danger, onConfirm, onCancel }: Props) {
  return (
    <Sheet title={title} onClose={onCancel}>
      <div class="stack">
        <div>{body}</div>
        <div class="row">
          <Button variant={danger ? "danger" : "filled"} onClick={onConfirm}>{confirmLabel}</Button>
          <Button variant="plain" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </Sheet>
  );
}
```

`Empty.tsx`:
```tsx
import { Icon } from "./Icon";

export function Empty({ text, check }: { text: string; check?: boolean }) {
  return (
    <div class="empty">
      {check ? <Icon name="check" /> : null}
      <div>{text}</div>
    </div>
  );
}
```

`Icon.tsx` (24 px viewBox, stroke icons, `currentColor`):
```tsx
const PATHS: Record<string, string> = {
  plan: "M4 5h16v4H4zM4 15h7v4H4zM13 15h7v4h-7z",
  inbox: "M3 13l2-8h14l2 8v6H3zM3 13h5l2 3h4l2-3h5",
  activity: "M12 3a9 9 0 1 0 9 9M12 7v5l3 3",
  spend: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  close: "M6 6l12 12M18 6L6 18",
  chevron: "M9 6l6 6-6 6",
  check: "M4 12l5 5L20 6",
  more: "M12 6h.01M12 12h.01M12 18h.01",
};

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return (
    <svg class="icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
```

`NodeRowItem.tsx`:
```tsx
import type { ComponentChildren } from "preact";
import type { NodeRow } from "../state";
import { openNode } from "../router";
import { Icon } from "./Icon";

export function NodeRowItem({ node, right, sub }: { node: NodeRow; right?: ComponentChildren; sub?: string }) {
  return (
    <button type="button" class="list-row" onClick={() => openNode(node.id)}>
      <span class="dot" style={{ background: `var(--st-${node.status})` }} />
      <span class="grow">
        <span class="title">#{node.id} {node.title}</span>
        {sub ? <span class="caption" style={{ display: "block" }}>{sub}</span> : null}
      </span>
      {right}
      <span class="chev"><Icon name="chevron" /></span>
    </button>
  );
}
```

- [ ] **Step 2: Write ui.css**

```css
.btn { border-radius: var(--radius); padding: 10px 16px; min-height: 44px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; font-weight: 500; }
.btn:disabled { opacity: .5; cursor: default; }
.btn-filled { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.btn-filled:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.btn-outline:hover:not(:disabled) { background: var(--surface-2); }
.btn-danger { color: var(--danger); border-color: var(--danger); background: transparent; }
.btn-danger:hover:not(:disabled) { background: color-mix(in srgb, var(--danger) 10%, var(--surface)); }
.btn-plain { border-color: transparent; background: transparent; color: var(--accent); }
.icon-btn { background: none; border: 0; padding: 10px; border-radius: var(--radius); cursor: pointer; color: var(--text-2); min-width: 44px; min-height: 44px; display: inline-flex; align-items: center; justify-content: center; }
.icon-btn:hover { background: var(--surface-2); }
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 500; color: var(--st-pending); background: color-mix(in srgb, var(--st-pending) 14%, var(--surface)); text-transform: capitalize; }
.pill.st-ready { color: var(--st-ready); background: color-mix(in srgb, var(--st-ready) 14%, var(--surface)); }
.pill.st-in_progress { color: var(--st-in_progress); background: color-mix(in srgb, var(--st-in_progress) 14%, var(--surface)); }
.pill.st-review { color: var(--st-review); background: color-mix(in srgb, var(--st-review) 14%, var(--surface)); }
.pill.st-awaiting_approval { color: var(--st-awaiting_approval); background: color-mix(in srgb, var(--st-awaiting_approval) 14%, var(--surface)); }
.pill.st-done, .pill.st-executing { color: var(--st-done); background: color-mix(in srgb, var(--st-done) 14%, var(--surface)); }
.pill.st-blocked, .pill.st-paused { color: var(--st-blocked); background: color-mix(in srgb, var(--st-blocked) 14%, var(--surface)); }
.pill.st-failed { color: var(--st-failed); background: color-mix(in srgb, var(--st-failed) 14%, var(--surface)); }
.pill.st-planning { color: var(--st-ready); background: color-mix(in srgb, var(--st-ready) 14%, var(--surface)); }
.tier { font-size: 12px; }
.segmented { display: inline-flex; background: var(--surface-2); border-radius: var(--radius); padding: 3px; gap: 2px; }
.segmented button { border: 0; background: transparent; padding: 8px 14px; border-radius: 8px; cursor: pointer; color: var(--text-2); min-height: 38px; transition: background 180ms ease-out; }
.segmented button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); font-weight: 500; }
.sheet-overlay { position: fixed; inset: 0; background: rgb(0 0 0 / .35); z-index: 20; display: flex; align-items: flex-end; justify-content: center; }
.sheet { background: var(--surface); width: 100%; max-height: calc(100dvh - env(safe-area-inset-top) - 24px); border-radius: var(--radius-lg) var(--radius-lg) 0 0; display: flex; flex-direction: column; box-shadow: var(--shadow); animation: sheet-up 180ms ease-out; }
.sheet-handle { width: 36px; height: 5px; border-radius: 3px; background: var(--border); margin: 8px auto 0; }
.sheet-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 8px 8px 16px; border-bottom: 1px solid var(--border); }
.sheet-head h2 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sheet-body { overflow: auto; padding: 16px; padding-bottom: calc(16px + env(safe-area-inset-bottom)); }
@keyframes sheet-up { from { transform: translateY(24px); opacity: 0; } to { transform: none; opacity: 1; } }
@media (min-width: 900px) {
  .sheet-overlay { align-items: stretch; justify-content: flex-end; }
  .sheet { width: 440px; max-height: 100dvh; height: 100dvh; border-radius: 0; animation: sheet-in 180ms ease-out; }
  .sheet-handle { display: none; }
  @keyframes sheet-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
}
.toasts { position: fixed; left: 0; right: 0; bottom: calc(var(--tab-h) + env(safe-area-inset-bottom) + 12px); display: flex; flex-direction: column; align-items: center; gap: 8px; z-index: 30; pointer-events: none; }
.toast { pointer-events: auto; background: var(--text); color: var(--bg); border: 0; border-radius: 999px; padding: 10px 18px; font-size: 13px; box-shadow: var(--shadow); max-width: 90vw; }
.toast.error { background: var(--danger); color: #fff; }
@media (min-width: 900px) { .toasts { bottom: 20px; } }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
```

- [ ] **Step 3: Write ui.test.tsx**

```tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { Sheet } from "../src/ui/Sheet";
import { Segmented } from "../src/ui/Segmented";
import { Pill } from "../src/ui/Pill";

test("Sheet closes on Escape and on the close button", () => {
  const onClose = vi.fn();
  render(<Sheet title="t" onClose={onClose}>body</Sheet>);
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByLabelText("close"));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test("Segmented reports the chosen value", () => {
  const onChange = vi.fn();
  render(<Segmented options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} value="a" onChange={onChange} />);
  fireEvent.click(screen.getByText("B"));
  expect(onChange).toHaveBeenCalledWith("b");
});

test("Pill shows the status in words", () => {
  render(<Pill status="in_progress" />);
  expect(screen.getByText("in progress")).toHaveClass("st-in_progress");
});
```

- [ ] **Step 4: Import `./ui/ui.css` in `main.tsx`** after `base.css`.

- [ ] **Step 5: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: UI kit (buttons, pills, segmented control, sheet, toasts)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 7: App shell — sidebar, tab bar, top bar, project menu, token banner

**Files:**
- Create: `dashboard/src/shell/Sidebar.tsx`, `TabBar.tsx`, `TopBar.tsx`, `ProjectMenu.tsx`, `TokenBanner.tsx`, `dashboard/src/shell/shell.css`
- Modify: `dashboard/src/app.tsx`, `dashboard/src/main.tsx`
- Test: `dashboard/test/shell.test.tsx`

**Interfaces:**
- Consumes: `state.ts`, `router.ts`, `client.ts`, `auth.ts`, `stream.ts`, UI kit.
- Produces: `App()` renders the shell with a `<main>` outlet that switches on `route.value.page`; pages are registered in `app.tsx` as `PAGES: Record<string, () => JSX.Element>` — later tasks add entries. `App` also renders `<NodeSheet/>` when `route.value.query.get("node")` is set (Task 9 provides it; until then a placeholder component `NodeSheetPlaceholder` in `app.tsx` renders nothing).
- `ProjectMenu({onClose})`: switch project; Pause / Resume / Close project (Close opens `CloseSheet`, Task 14; until then it toasts "close: coming in Task 14").

- [ ] **Step 1: Write shell.test.tsx**

```tsx
import { render, screen } from "@testing-library/preact";
import { App } from "../src/app";
import { projects, projectId, authed, inboxCount } from "../src/state";

beforeEach(() => {
  projects.value = [{ id: 1, goal: "build the thing", phase: "planning" }];
  projectId.value = 1;
  authed.value = true;
  inboxCount.value = 3;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => ({ nodes: [], edges: [] }), text: async () => "" })));
  vi.stubGlobal("EventSource", undefined);
});
afterEach(() => vi.unstubAllGlobals());

test("the shell shows the project, the inbox badge and no token banner when signed in", () => {
  render(<App />);
  expect(screen.getAllByText("build the thing").length).toBeGreaterThan(0);
  expect(screen.getAllByText("3").length).toBeGreaterThan(0);
  expect(screen.queryByPlaceholderText(/api token/)).toBeNull();
});

test("read-only shows the token banner", () => {
  authed.value = false;
  render(<App />);
  expect(screen.getByPlaceholderText(/api token/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it** — `cd /home/eugene/projects/muvue/dashboard && npm test -- shell`. Expected: FAIL (no project text).

- [ ] **Step 3: Write the shell components**

`Sidebar.tsx`:
```tsx
import { useState } from "preact/hooks";
import { route, navigate } from "../router";
import { currentProject, inboxCount, authed, protocolVersion } from "../state";
import { Icon } from "../ui/Icon";
import { Pill } from "../ui/Pill";
import { ProjectMenu } from "./ProjectMenu";

export const NAV = [
  { page: "plan", label: "Plan", icon: "plan" },
  { page: "inbox", label: "Inbox", icon: "inbox" },
  { page: "activity", label: "Activity", icon: "activity" },
  { page: "spend", label: "Spend", icon: "spend" },
] as const;

export function isActive(page: string, current: string): boolean {
  return current === page || (page === "plan" && current === "spec");
}

export function Sidebar() {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <aside class="sidebar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow">
          <span class="project-goal">{p ? p.goal : "no project"}</span>
          {p ? <Pill status={p.phase} /> : null}
        </span>
        <Icon name="more" />
      </button>
      <nav>
        {NAV.map((n) => (
          <button type="button" class={"nav-item" + (isActive(n.page, route.value.page) ? " on" : "")} onClick={() => navigate("#/" + n.page)}>
            <Icon name={n.icon} />
            <span class="grow">{n.label}</span>
            {n.page === "inbox" && inboxCount.value > 0 ? <span class="badge">{inboxCount.value}</span> : null}
          </button>
        ))}
      </nav>
      <div class="sidebar-foot caption">
        <div>{authed.value ? "signed in" : "read-only"}</div>
        {protocolVersion.value !== null ? <div>protocol v{protocolVersion.value}</div> : null}
      </div>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </aside>
  );
}
```

`TabBar.tsx`:
```tsx
import { route, navigate } from "../router";
import { inboxCount } from "../state";
import { Icon } from "../ui/Icon";
import { NAV, isActive } from "./Sidebar";

export function TabBar() {
  return (
    <nav class="tabbar">
      {NAV.map((n) => (
        <button type="button" class={"tab" + (isActive(n.page, route.value.page) ? " on" : "")} onClick={() => navigate("#/" + n.page)} aria-label={n.label}>
          <span class="tab-icon"><Icon name={n.icon} />{n.page === "inbox" && inboxCount.value > 0 ? <span class="badge">{inboxCount.value}</span> : null}</span>
          <span class="tab-label">{n.label}</span>
        </button>
      ))}
    </nav>
  );
}
```

`TopBar.tsx`:
```tsx
import { useState } from "preact/hooks";
import { currentProject } from "../state";
import { Pill } from "../ui/Pill";
import { Icon } from "../ui/Icon";
import { ProjectMenu } from "./ProjectMenu";

export function TopBar({ onSearch }: { onSearch: () => void }) {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <header class="topbar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow project-goal">{p ? p.goal : "no project"}</span>
        {p ? <Pill status={p.phase} /> : null}
      </button>
      <button type="button" class="icon-btn" aria-label="search" onClick={onSearch}><Icon name="search" /></button>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </header>
  );
}
```

`ProjectMenu.tsx`:
```tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast, toastError } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { Confirm } from "../ui/Confirm";
import { Pill } from "../ui/Pill";
import { CloseSheet } from "../project/CloseSheet";

export function ProjectMenu({ onClose }: { onClose: () => void }) {
  const [confirmPause, setConfirmPause] = useState(false);
  const [closing, setClosing] = useState(false);
  const p = currentProject.value;

  async function pause() {
    if (!p) return;
    try {
      const r = await post<{ result?: { stopped_runners?: unknown[] }; stopped_runners?: unknown[] }>(routes.projectPause(p.id));
      const stopped = (r.result ?? r).stopped_runners ?? [];
      toast(`paused; stopped ${stopped.length} runner process(es)`);
      refresh();
      onClose();
    } catch (e) { toastError(e); }
  }
  async function resume() {
    if (!p) return;
    try { await post(routes.projectResume(p.id)); toast("resumed"); refresh(); onClose(); } catch (e) { toastError(e); }
  }

  if (closing && p) return <CloseSheet projectId={p.id} onClose={() => { setClosing(false); onClose(); }} />;
  if (confirmPause) {
    return <Confirm title="Pause project?" body="Running agents are stopped and their tasks go back to ready." confirmLabel="Pause" danger onConfirm={pause} onCancel={() => setConfirmPause(false)} />;
  }
  return (
    <Sheet title="Project" onClose={onClose}>
      <div class="stack">
        <div class="list">
          {projects.value.map((pr) => (
            <button type="button" class={"list-row" + (pr.id === projectId.value ? " on" : "")} onClick={() => { projectId.value = pr.id; refresh(); onClose(); }}>
              <span class="grow"><span class="title">#{pr.id} {pr.goal}</span></span>
              <Pill status={pr.phase} />
            </button>
          ))}
        </div>
        {authed.value && p ? (
          <div class="actions">
            {p.phase !== "paused" && p.phase !== "closed" ? <Button variant="danger" onClick={() => setConfirmPause(true)}>Pause</Button> : null}
            {p.phase === "paused" ? <Button variant="filled" onClick={resume}>Resume</Button> : null}
            {p.phase !== "closed" ? <Button onClick={() => setClosing(true)}>Close project…</Button> : null}
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}
```

Until Task 14 exists, create a stub `dashboard/src/project/CloseSheet.tsx`:
```tsx
import { Sheet } from "../ui/Sheet";
export function CloseSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  return <Sheet title={`Close project #${projectId}`} onClose={onClose}><p class="muted">close preview arrives in Task 14</p></Sheet>;
}
```

`TokenBanner.tsx`:
```tsx
import { useState } from "preact/hooks";
import { setToken } from "../api/client";
import { checkAuth } from "../api/auth";
import { setAuthed, toastError } from "../state";
import { Button } from "../ui/Button";

export function TokenBanner() {
  const [value, setValue] = useState("");
  async function use() {
    setToken(value.trim());
    setValue("");
    try { setAuthed(await checkAuth()); } catch (e) { toastError(e); }
  }
  return (
    <form class="token-banner" onSubmit={(e) => { e.preventDefault(); void use(); }}>
      <div class="caption">Read-only. Paste the api token printed by <code>muvue serve</code> to act. It stays in this tab's memory only.</div>
      <div class="row">
        <input type="password" placeholder="api token printed by muvue serve" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
        <Button type="submit" variant="filled">Use token</Button>
      </div>
    </form>
  );
}
```

`shell.css`:
```css
.app { min-height: 100dvh; display: flex; flex-direction: column; }
main { flex: 1; padding-bottom: calc(var(--tab-h) + env(safe-area-inset-bottom)); }
.topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 4px; padding: 8px 8px 8px 16px; padding-top: calc(8px + env(safe-area-inset-top)); background: color-mix(in srgb, var(--bg) 85%, transparent); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); }
.project-btn { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; background: none; border: 0; padding: 8px; border-radius: var(--radius); cursor: pointer; text-align: left; min-height: 44px; }
.project-btn:hover { background: var(--surface-2); }
.project-btn .grow { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; }
.project-goal { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tabbar { position: fixed; left: 0; right: 0; bottom: 0; height: calc(var(--tab-h) + env(safe-area-inset-bottom)); padding-bottom: env(safe-area-inset-bottom); display: flex; background: color-mix(in srgb, var(--surface) 88%, transparent); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border-top: 1px solid var(--border); z-index: 10; }
.tab { flex: 1; border: 0; background: none; color: var(--text-2); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; cursor: pointer; }
.tab.on { color: var(--accent); }
.tab-icon { position: relative; }
.tab-label { font-size: 11px; }
.badge { background: var(--danger); color: #fff; border-radius: 999px; font-size: 11px; min-width: 18px; height: 18px; padding: 0 5px; display: inline-flex; align-items: center; justify-content: center; }
.tab-icon .badge { position: absolute; top: -6px; right: -12px; }
.sidebar { display: none; }
.token-banner { margin: 16px 16px 0; padding: 12px 16px; border-radius: var(--radius-lg); background: var(--surface-2); border: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
.token-banner .row { flex-wrap: nowrap; }
.token-banner input { flex: 1; }
@media (min-width: 900px) {
  .app { flex-direction: row; }
  .topbar, .tabbar { display: none; }
  main { padding-bottom: 0; }
  .sidebar { display: flex; flex-direction: column; width: var(--sidebar-w); flex: none; border-right: 1px solid var(--border); background: var(--surface); padding: 12px; gap: 8px; position: sticky; top: 0; height: 100dvh; }
  .sidebar nav { display: flex; flex-direction: column; gap: 2px; }
  .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: var(--radius); border: 0; background: none; cursor: pointer; text-align: left; color: var(--text); }
  .nav-item:hover { background: var(--surface-2); }
  .nav-item.on { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); color: var(--accent); font-weight: 500; }
  .nav-item .grow { flex: 1; }
  .sidebar-foot { margin-top: auto; padding: 8px 12px; }
  .list-row.on { background: color-mix(in srgb, var(--accent) 10%, var(--surface)); }
}
```

- [ ] **Step 4: Write app.tsx and main.tsx**

`app.tsx`:
```tsx
import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";
import { route } from "./router";
import { authed } from "./state";
import { Sidebar } from "./shell/Sidebar";
import { TabBar } from "./shell/TabBar";
import { TopBar } from "./shell/TopBar";
import { TokenBanner } from "./shell/TokenBanner";
import { Toasts } from "./ui/Toasts";

// Pages register here; Tasks 8-13 add their entries.
export const PAGES: Record<string, () => JSX.Element> = {
  plan: () => <div class="page"><h1 class="page-title">Plan</h1></div>,
};

function NodeSheetPlaceholder() { return null; }

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
      {nodeId ? <NodeSheetPlaceholder /> : null}
      {palette ? null : null}
      <Toasts />
    </div>
  );
}
```

`main.tsx`:
```tsx
import "./styles/tokens.css";
import "./styles/base.css";
import "./ui/ui.css";
import "./shell/shell.css";
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
  effect(() => { void refreshTick.value; loadProjects().catch(toastError); });
  connectStream();
  render(<App />, document.getElementById("root")!);
}

boot().catch((e) => {
  document.getElementById("root")!.textContent = "muvue dashboard failed to start: " + (e instanceof Error ? e.message : String(e));
});
```

- [ ] **Step 5: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass. The smoke test from Task 1 now needs `projects.value` etc.; update it to assert `screen.getAllByText("Plan").length > 0` instead of the h1.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: app shell with sidebar, tab bar, project menu and token banner

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 8: Plan page — layout, diagram, list, spec card

**Files:**
- Create: `dashboard/src/plan/layout.ts`, `Diagram.tsx`, `TaskList.tsx`, `SpecCard.tsx`, `PlanPage.tsx`, `dashboard/src/plan/plan.css`
- Modify: `dashboard/src/app.tsx` (`PAGES.plan = PlanPage`), `dashboard/src/main.tsx` (import `./plan/plan.css`)
- Test: `dashboard/test/layout.test.ts`, `dashboard/test/SpecCard.test.tsx`

**Interfaces:**
- Consumes: `GET /graph?project_id=` → `{nodes: NodeRow[], edges: {from: number, to: number, kind: "parent"|"dep"}[]}`; `GET /nodes/{id}` → `{node: NodeRow & {body_md: string|null, ...}}`; `POST /nodes/{id}/approve {target: "spec"}`; `POST /nodes/{project_id}/approve {target: "gate2"}`.
- Produces: `layout(graph): {pos: Record<number, {x, y, layer, i}>, width, height}` with `BOX_W = 200, BOX_H = 52, GAP_X = 28, GAP_Y = 64, PAD = 16`. `Diagram({graph})`, `TaskList({nodes})`, `SpecCard({spec, taskCount})`, `PlanPage()`.

- [ ] **Step 1: Write layout.test.ts**

```ts
import { layout } from "../src/plan/layout";

const n = (id: number) => ({ id, project_id: 1, parent_id: null, kind: "task", title: "t" + id, status: "ready" as const, risk_tier: "low" as const, owner: null });

test("a dependency sits in the layer above what waits on it", () => {
  const lay = layout({ nodes: [n(1), n(2), n(3)], edges: [{ from: 1, to: 2, kind: "parent" }, { from: 2, to: 3, kind: "dep" }] });
  expect(lay.pos[1]!.layer).toBe(0);
  expect(lay.pos[2]!.layer).toBe(1);
  expect(lay.pos[3]!.layer).toBe(2);
  expect(lay.pos[3]!.y).toBeGreaterThan(lay.pos[2]!.y);
});

test("siblings keep id order and the widest layer sets the width", () => {
  const lay = layout({ nodes: [n(1), n(2), n(3)], edges: [{ from: 1, to: 2, kind: "parent" }, { from: 1, to: 3, kind: "parent" }] });
  expect(lay.pos[2]!.x).toBeLessThan(lay.pos[3]!.x);
  expect(lay.width).toBe(16 * 2 + 2 * 200 + 28);
});

test("a cycle does not hang", () => {
  const lay = layout({ nodes: [n(1), n(2)], edges: [{ from: 1, to: 2, kind: "dep" }, { from: 2, to: 1, kind: "dep" }] });
  expect(Object.keys(lay.pos)).toHaveLength(2);
});
```

- [ ] **Step 2: Run it** — `cd /home/eugene/projects/muvue/dashboard && npm test -- layout`. Expected: FAIL, module not found.

- [ ] **Step 3: Write layout.ts** (a port of the old `layout()`; behaviour unchanged, sizes updated)

```ts
import type { NodeRow } from "../state";

export type Edge = { from: number; to: number; kind: "parent" | "dep" };
export type Graph = { nodes: NodeRow[]; edges: Edge[] };
export type Placed = { x: number; y: number; layer: number; i: number };

export const BOX_W = 200, BOX_H = 52, GAP_X = 28, GAP_Y = 64, PAD = 16;

// A node's layer is the longest path to it over parent and dependency
// edges, so a dependency always sits above what waits on it. Within a
// layer, nodes are ordered by the mean position of their predecessors,
// which keeps siblings together and cuts crossings.
export function layout(graph: Graph): { pos: Record<number, Placed>; width: number; height: number } {
  const incoming: Record<number, number[]> = {};
  for (const n of graph.nodes) incoming[n.id] = [];
  for (const e of graph.edges) incoming[e.to]?.push(e.from);
  const depth: Record<number, number> = {};
  function depthOf(id: number, seen: Set<number>): number {
    if (depth[id] !== undefined) return depth[id]!;
    if (seen.has(id)) return 0; // a cycle would be a data bug; don't hang on it
    seen.add(id);
    let d = 0;
    for (const from of incoming[id] ?? []) d = Math.max(d, depthOf(from, seen) + 1);
    depth[id] = d;
    return d;
  }
  for (const n of graph.nodes) depthOf(n.id, new Set());
  const layers: number[][] = [];
  for (const n of graph.nodes) (layers[depth[n.id]!] ??= []).push(n.id);
  const pos: Record<number, Placed> = {};
  layers.forEach((layer, y) => {
    const center = (id: number) => {
      const xs = (incoming[id] ?? []).filter((f) => pos[f]).map((f) => pos[f]!.i);
      return xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : id;
    };
    layer.sort((a, b) => center(a) - center(b) || a - b);
    layer.forEach((id, i) => { pos[id] = { i, layer: y, x: 0, y: 0 }; });
  });
  const widest = Math.max(1, ...layers.map((l) => l.length));
  const width = PAD * 2 + widest * BOX_W + (widest - 1) * GAP_X;
  layers.forEach((layer) => {
    const rowWidth = layer.length * BOX_W + (layer.length - 1) * GAP_X;
    const left = (width - rowWidth) / 2;
    layer.forEach((id, i) => {
      pos[id]!.x = left + i * (BOX_W + GAP_X);
      pos[id]!.y = PAD + pos[id]!.layer * (BOX_H + GAP_Y);
    });
  });
  return { pos, width, height: PAD * 2 + layers.length * BOX_H + Math.max(0, layers.length - 1) * GAP_Y };
}
```

- [ ] **Step 4: Run** — `npm test -- layout`. Expected: PASS.

- [ ] **Step 5: Write Diagram.tsx**

```tsx
import { layout, BOX_W, BOX_H, type Graph } from "./layout";
import { openNode } from "../router";

export function Diagram({ graph }: { graph: Graph }) {
  const lay = layout(graph);
  return (
    <div class="dag-wrap">
      <svg class="dag" width={lay.width} height={lay.height} viewBox={`0 0 ${lay.width} ${lay.height}`} role="img" aria-label="plan diagram">
        {graph.edges.map((e) => {
          const a = lay.pos[e.from]!, b = lay.pos[e.to]!;
          const x1 = a.x + BOX_W / 2, y1 = a.y + BOX_H, x2 = b.x + BOX_W / 2, y2 = b.y, mid = (y1 + y2) / 2;
          return <path class={"edge " + e.kind} d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`} />;
        })}
        {graph.nodes.map((n) => {
          const p = lay.pos[n.id]!;
          const text = `#${n.id} ${n.title}`;
          return (
            <g class="node" transform={`translate(${p.x},${p.y})`} tabIndex={0} onClick={() => openNode(n.id)} onKeyDown={(e) => { if (e.key === "Enter") openNode(n.id); }}>
              <title>{`${text} (${n.status}, ${n.risk_tier} risk)`}</title>
              <rect width={BOX_W} height={BOX_H} rx={10} />
              <rect class="bar" width={4} height={BOX_H} rx={2} style={{ fill: `var(--st-${n.status})` }} />
              <circle cx={18} cy={18} r={5} style={{ fill: `var(--st-${n.status})` }} />
              <text x={30} y={22}>{text.length > 24 ? text.slice(0, 23) + "…" : text}</text>
              <text x={30} y={40} class="sub">{`${n.kind} · ${n.status.replace(/_/g, " ")} · ${n.risk_tier}`}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
```

- [ ] **Step 6: Write TaskList.tsx**

```tsx
import type { NodeRow, NodeStatus } from "../state";
import { NodeRowItem } from "../ui/NodeRowItem";
import { Tier } from "../ui/Pill";

export const STATUS_ORDER: NodeStatus[] = ["review", "awaiting_approval", "blocked", "in_progress", "ready", "pending", "done", "failed"];

export function TaskList({ nodes }: { nodes: NodeRow[] }) {
  const groups = STATUS_ORDER.map((s) => ({ status: s, items: nodes.filter((n) => n.status === s) })).filter((g) => g.items.length);
  return (
    <div class="stack">
      {groups.map((g) => (
        <section>
          <h3 class="group-head" style={{ color: `var(--st-${g.status})` }}>{g.status.replace(/_/g, " ")} · {g.items.length}</h3>
          <div class="list">
            {g.items.map((n) => <NodeRowItem node={n} right={<Tier tier={n.risk_tier} />} sub={n.block_reason ? "blocked: " + n.block_reason : undefined} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Write SpecCard.test.tsx**

```tsx
import { render, screen } from "@testing-library/preact";
import { SpecCard } from "../src/plan/SpecCard";
import { authed, projects, projectId } from "../src/state";

const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: "pending" as const, risk_tier: "low" as const, owner: null, body_md: "line one\nline two" };

beforeEach(() => { authed.value = true; projects.value = [{ id: 1, goal: "g", phase: "planning" }]; projectId.value = 1; });

test("a pending spec offers Approve spec", () => {
  render(<SpecCard spec={spec} taskCount={0} />);
  expect(screen.getByText("Approve spec")).toBeInTheDocument();
  expect(screen.queryByText("Approve task list")).toBeNull();
});

test("an approved spec with tasks in planning offers Approve task list", () => {
  render(<SpecCard spec={{ ...spec, status: "ready" }} taskCount={3} />);
  expect(screen.getByText("Approve task list")).toBeInTheDocument();
});

test("read-only shows neither button", () => {
  authed.value = false;
  render(<SpecCard spec={spec} taskCount={3} />);
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.queryByText("Approve task list")).toBeNull();
});
```

- [ ] **Step 8: Write SpecCard.tsx**

```tsx
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, currentProject, refresh, toast, toastError, type NodeRow } from "../state";
import { navigate } from "../router";
import { Button } from "../ui/Button";
import { Pill } from "../ui/Pill";

export type SpecNode = NodeRow & { body_md: string | null };

export function SpecCard({ spec, taskCount }: { spec: SpecNode; taskCount: number }) {
  const p = currentProject.value;
  const canApproveSpec = authed.value && spec.status === "pending";
  const canApproveTasks = authed.value && spec.status !== "pending" && taskCount > 0 && p?.phase === "planning";
  const preview = (spec.body_md || "").split("\n").filter((l) => l.trim()).slice(0, 3).join("\n");

  async function approveSpec() {
    try { await post(routes.nodeApprove(spec.id), { target: "spec" }); toast("spec approved"); refresh(); } catch (e) { toastError(e); }
  }
  async function approveTasks() {
    if (!p) return;
    try { await post(routes.nodeApprove(p.id), { target: "gate2" }); toast("task list approved; criteria frozen"); refresh(); } catch (e) { toastError(e); }
  }

  return (
    <section class="card stack">
      <div class="row between">
        <h2>#{spec.id} {spec.title}</h2>
        <Pill status={spec.status} />
      </div>
      {preview ? <pre class="spec-preview">{preview}</pre> : null}
      <div class="actions">
        {canApproveSpec ? <Button variant="filled" onClick={approveSpec}>Approve spec</Button> : null}
        {canApproveTasks ? <Button variant="filled" onClick={approveTasks}>Approve task list</Button> : null}
        <Button variant="plain" onClick={() => navigate("#/spec/" + spec.id)}>Read spec</Button>
      </div>
      {canApproveTasks ? <div class="caption">Approving freezes each task's acceptance criteria.</div> : null}
    </section>
  );
}
```

- [ ] **Step 9: Write PlanPage.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { projectId, refreshTick, toastError, type NodeRow } from "../state";
import { Segmented } from "../ui/Segmented";
import { Empty } from "../ui/Empty";
import { Diagram } from "./Diagram";
import { TaskList } from "./TaskList";
import { SpecCard, type SpecNode } from "./SpecCard";
import type { Graph } from "./layout";

const isPhone = () => typeof window !== "undefined" && window.innerWidth < 900;

export function PlanPage() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [specs, setSpecs] = useState<SpecNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState(isPhone() ? "list" : "diagram");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const g = await api<Graph>(routes.graph(projectId.value));
        const specRows = g.nodes.filter((n) => n.kind === "spec");
        const full = await Promise.all(specRows.map((s) => api<{ node: SpecNode }>(routes.node(s.id)).then((d) => d.node)));
        if (!alive) return;
        setGraph(g); setSpecs(full); setError(null);
      } catch (e) { if (alive) { setError(e instanceof Error ? e.message : String(e)); toastError(e); } }
    })();
    return () => { alive = false; };
  }, [projectId.value, refreshTick.value]);

  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!graph) return <div class="page"><p class="muted">loading…</p></div>;
  const tasks = graph.nodes.filter((n) => n.kind !== "spec");
  const taskGraph: Graph = { nodes: graph.nodes, edges: graph.edges };
  return (
    <div class="page stack">
      <h1 class="page-title">Plan</h1>
      {specs.length ? specs.map((s) => <SpecCard spec={s} taskCount={tasks.length} />) : <Empty text="No spec yet. Create one with muvue spec <project>." />}
      {tasks.length ? (
        <>
          <div class="row between">
            <h2>Tasks · {tasks.length}</h2>
            <Segmented options={[{ value: "diagram", label: "Diagram" }, { value: "list", label: "List" }]} value={mode} onChange={setMode} />
          </div>
          {mode === "diagram" ? <Diagram graph={taskGraph} /> : <TaskList nodes={tasks} />}
        </>
      ) : specs.length ? <Empty text="No tasks yet. They appear here once the spec is decomposed." /> : null}
    </div>
  );
}
```

- [ ] **Step 10: Write plan.css**

```css
.dag-wrap { overflow: auto; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 8px; -webkit-overflow-scrolling: touch; }
svg.dag .node rect { fill: var(--surface-2); stroke: var(--border); cursor: pointer; }
svg.dag .node rect.bar { stroke: none; }
svg.dag .node:hover rect:first-of-type, svg.dag .node:focus rect:first-of-type { stroke: var(--accent); outline: none; }
svg.dag .node text { fill: var(--text); font-size: 12px; font-family: var(--font); pointer-events: none; }
svg.dag .node text.sub { fill: var(--text-2); font-size: 10px; }
svg.dag path.edge { fill: none; stroke: var(--border); stroke-width: 1.5; }
svg.dag path.edge.dep { stroke: var(--accent); stroke-dasharray: 5 4; }
.group-head { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; margin: 4px 0 6px; }
.spec-preview { max-height: 80px; overflow: hidden; }
```

- [ ] **Step 11: Register** — in `app.tsx` replace the `plan` placeholder with `plan: PlanPage` (import it). Import `./plan/plan.css` in `main.tsx`.

- [ ] **Step 12: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 13: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: Plan page with spec card, diagram and grouped task list

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 9: Task sheet — actions, overview, diff, logs, agent picker

**Files:**
- Create: `dashboard/src/node/diff.ts`, `NodeSheet.tsx`, `Actions.tsx`, `Overview.tsx`, `DiffView.tsx`, `Logs.tsx`, `StartPicker.tsx`
- Modify: `dashboard/src/app.tsx` (render `NodeSheet` instead of the placeholder)
- Test: `dashboard/test/diff.test.ts`, `dashboard/test/NodeSheet.test.tsx`

**Interfaces:**
- Consumes: `GET /nodes/{id}` → `{node, notes: {kind, pinned, created_at, text}[], commits: {sha}[], predicted_touches: string[], verification: "checked_by_muvue"|"unverified"|"human"}`; `GET /nodes/{id}/diff` → `{source: "commits"|"worktree"|"none", diff: string, truncated: boolean}`; `GET /nodes/{id}/logs?lines=200` → text; `POST /nodes/{id}/approve {target}`, `POST /nodes/{id}/reject {feedback}`, `POST /nodes/{id}/start?agent=` → `{spawned: {pid, log}}`; `GET /agents` → `{agents: string[]}`.
- Produces: `classifyDiffLine(line): "d-meta"|"d-hunk"|"d-add"|"d-del"|""`; `NodeSheet()` reads `route.value.query.get("node")`.

- [ ] **Step 1: Write diff.test.ts**

```ts
import { classifyDiffLine } from "../src/node/diff";

test("diff lines classify", () => {
  expect(classifyDiffLine("diff --git a b")).toBe("d-meta");
  expect(classifyDiffLine("+++ b/x")).toBe("d-meta");
  expect(classifyDiffLine("@@ -1 +1 @@")).toBe("d-hunk");
  expect(classifyDiffLine("+added")).toBe("d-add");
  expect(classifyDiffLine("-removed")).toBe("d-del");
  expect(classifyDiffLine(" context")).toBe("");
});
```

- [ ] **Step 2: Write diff.ts**

```ts
export function classifyDiffLine(line: string): "d-meta" | "d-hunk" | "d-add" | "d-del" | "" {
  if (/^(\+\+\+|---|diff |commit |index )/.test(line)) return "d-meta";
  if (line.startsWith("@@")) return "d-hunk";
  if (line.startsWith("+")) return "d-add";
  if (line.startsWith("-")) return "d-del";
  return "";
}
```

- [ ] **Step 3: Write NodeSheet.test.tsx**

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { NodeSheet } from "../src/node/NodeSheet";
import { authed } from "../src/state";
import { resetRouteFromLocation } from "../src/router";

function detail(status: string, kind = "task") {
  return { node: { id: 7, project_id: 1, parent_id: 1, kind, title: "Do it", status, risk_tier: "low", owner: null, body_md: "body", criteria_json: '["a","b"]', criteria_mode: "auto", summary: null, block_reason: null }, notes: [], commits: [], predicted_touches: [], verification: "checked_by_muvue" };
}

function stubApi(status: string, kind = "task") {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = url.startsWith("/nodes/7/diff") ? { source: "none", diff: "", truncated: false }
      : url === "/agents" ? { agents: ["claude", "fake"] } : url.startsWith("/nodes/7/approve") || url.startsWith("/nodes/7/reject") ? { ok: 1 } : detail(status, kind);
    const isText = url.startsWith("/nodes/7/logs");
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": isText ? "text/plain" : "application/json" }), json: async () => json, text: async () => "log line" };
  }));
  return calls;
}

beforeEach(() => { authed.value = true; window.location.hash = "#/plan?node=7"; resetRouteFromLocation(); });
afterEach(() => vi.unstubAllGlobals());

test("a pending spec shows Approve spec above the segments", async () => {
  stubApi("pending", "spec");
  render(<NodeSheet />);
  const btn = await screen.findByText("Approve spec");
  const overview = screen.getByText("Overview");
  expect(btn.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("review shows Approve and Reject; Reject posts the feedback", async () => {
  const calls = stubApi("review");
  render(<NodeSheet />);
  await screen.findByText("Approve");
  fireEvent.click(screen.getByText("Reject"));
  const ta = screen.getByPlaceholderText("what should change?");
  fireEvent.input(ta, { target: { value: "more tests" } });
  fireEvent.click(screen.getByText("Send"));
  await waitFor(() => expect(calls.some((c) => c.url === "/nodes/7/reject" && c.init?.body === '{"feedback":"more tests"}')).toBe(true));
});

test("ready shows Start with agent, and the picker lists agents", async () => {
  stubApi("ready");
  render(<NodeSheet />);
  fireEvent.click(await screen.findByText("Start with agent"));
  expect(await screen.findByText("claude")).toBeInTheDocument();
});

test("read-only shows no actions", async () => {
  authed.value = false;
  stubApi("review");
  render(<NodeSheet />);
  await screen.findByText("Overview");
  expect(screen.queryByText("Approve")).toBeNull();
});
```

- [ ] **Step 4: Run** — `npm test -- NodeSheet`. Expected: FAIL, module not found.

- [ ] **Step 5: Write the components**

`Actions.tsx`:
```tsx
import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { StartPicker } from "./StartPicker";
import type { NodeDetail } from "./NodeSheet";

export function Actions({ detail, onDone }: { detail: NodeDetail; onDone: () => void }) {
  const n = detail.node;
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [starting, setStarting] = useState(false);
  if (!authed.value) return null;

  async function approve(target: string, label: string) {
    try { await post(routes.nodeApprove(n.id), { target }); toast(label); refresh(); onDone(); } catch (e) { toastError(e); }
  }
  async function reject() {
    if (!feedback.trim()) return;
    try { await post(routes.nodeReject(n.id), { feedback }); toast("sent back with feedback"); refresh(); onDone(); } catch (e) { toastError(e); }
  }

  const buttons = [];
  if (n.status === "review") {
    buttons.push(<Button variant="filled" onClick={() => approve("review", "approved")}>Approve</Button>);
    buttons.push(<Button variant="danger" onClick={() => setRejecting(true)}>Reject</Button>);
  }
  if (n.status === "awaiting_approval") buttons.push(<Button variant="filled" onClick={() => approve("node", "criteria approved")}>Approve changed criteria</Button>);
  if (n.kind === "spec" && n.status === "pending") buttons.push(<Button variant="filled" onClick={() => approve("spec", "spec approved")}>Approve spec</Button>);
  if (n.status === "ready") buttons.push(<Button variant="filled" onClick={() => setStarting(true)}>Start with agent</Button>);
  if (!buttons.length) return null;

  return (
    <div class="stack tight">
      <div class="actions">{buttons}</div>
      {rejecting ? (
        <div class="stack tight">
          <textarea placeholder="what should change?" value={feedback} onInput={(e) => setFeedback((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button variant="danger" onClick={reject}>Send</Button><Button variant="plain" onClick={() => setRejecting(false)}>Cancel</Button></div>
        </div>
      ) : null}
      {starting ? <StartPicker nodeId={n.id} onClose={() => setStarting(false)} onStarted={onDone} /> : null}
    </div>
  );
}
```

`StartPicker.tsx`:
```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";

export function StartPicker({ nodeId, onClose, onStarted }: { nodeId: number; onClose: () => void; onStarted: () => void }) {
  const [agents, setAgents] = useState<string[] | null>(null);
  useEffect(() => { api<{ agents: string[] }>(routes.agents()).then((r) => setAgents(r.agents), toastError); }, []);
  async function start(agent: string) {
    try {
      const r = await post<{ spawned: { pid: number; log: string } }>(routes.nodeStart(nodeId, agent));
      toast(`started runner pid ${r.spawned.pid}`);
      refresh(); onStarted();
    } catch (e) { toastError(e); }
  }
  return (
    <div class="card stack tight">
      <div class="caption">Agent from config.toml [agents.*]</div>
      {agents === null ? <p class="muted">loading…</p> : agents.length ? (
        <div class="list">{agents.map((a) => <button type="button" class="list-row" onClick={() => start(a)}><span class="grow title">{a}</span></button>)}</div>
      ) : <p class="muted">no agents configured</p>}
      <div class="actions"><Button variant="plain" onClick={onClose}>Cancel</Button></div>
    </div>
  );
}
```

`Overview.tsx`:
```tsx
import type { NodeDetail } from "./NodeSheet";

const VERIFICATION: Record<string, string> = { checked_by_muvue: "checked by muvue", unverified: "unverified", human: "human" };

export function Overview({ detail }: { detail: NodeDetail }) {
  const n = detail.node;
  let criteria: unknown[] = [];
  try { criteria = JSON.parse(n.criteria_json || "[]"); } catch { criteria = [n.criteria_json]; }
  return (
    <div class="stack">
      {n.body_md ? <section><h3>Body</h3><pre>{n.body_md}</pre></section> : null}
      <section>
        <h3>Criteria <span class="caption">({n.criteria_mode}, {VERIFICATION[detail.verification] ?? detail.verification})</span></h3>
        {criteria.length ? <ul class="criteria">{criteria.map((c) => <li>{typeof c === "string" ? c : JSON.stringify(c)}</li>)}</ul> : <p class="muted">none</p>}
      </section>
      {n.summary ? <section><h3>Summary</h3><pre>{n.summary}</pre></section> : null}
      <section><h3>Predicted touches</h3><pre>{detail.predicted_touches.join("\n") || "none"}</pre></section>
      <section class="stack tight">
        <h3>Notes</h3>
        {detail.notes.length ? detail.notes.map((note) => (
          <div class="card">
            <div class="caption">{note.kind}{note.pinned ? " · pinned" : ""} · {note.created_at}</div>
            <div>{note.text}</div>
          </div>
        )) : <p class="muted">none</p>}
      </section>
      <section><h3>Commits</h3><pre class="mono">{detail.commits.map((c) => c.sha.slice(0, 12)).join("\n") || "none"}</pre></section>
    </div>
  );
}
```

`DiffView.tsx`:
```tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { classifyDiffLine } from "./diff";

const LABEL: Record<string, string> = { commits: "patches of the linked commits", worktree: "worktree against its branch point", none: "nothing committed yet" };

export function DiffView({ nodeId }: { nodeId: number }) {
  const [d, setD] = useState<{ source: string; diff: string; truncated: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<{ source: string; diff: string; truncated: boolean }>(routes.nodeDiff(nodeId)).then(setD, (e) => setError(e.message)); }, [nodeId]);
  if (error) return <div class="callout danger">{error}</div>;
  if (!d) return <p class="muted">loading…</p>;
  return (
    <div class="stack tight">
      <div class="caption">{LABEL[d.source] ?? d.source}{d.truncated ? " (truncated)" : ""}</div>
      <pre class="scroll-x">{d.diff ? d.diff.split("\n").map((line) => <span class={classifyDiffLine(line)}>{line + "\n"}</span>) : "(no diff)"}</pre>
    </div>
  );
}
```

`Logs.tsx`:
```tsx
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick } from "../state";
import { Button } from "../ui/Button";

export function Logs({ nodeId }: { nodeId: number }) {
  const [text, setText] = useState("loading…");
  const pre = useRef<HTMLPreElement>(null);
  function load() {
    api<string>(routes.nodeLogs(nodeId)).then((t) => { setText(t || "(no driver output yet)"); requestAnimationFrame(() => { if (pre.current) pre.current.scrollTop = pre.current.scrollHeight; }); }, (e) => setText(e.message));
  }
  useEffect(load, [nodeId, refreshTick.value]);
  return (
    <div class="stack tight">
      <div class="row between"><span class="caption">last 200 lines</span><Button variant="plain" onClick={load}>Refresh</Button></div>
      <pre ref={pre} class="scroll-x logs">{text}</pre>
    </div>
  );
}
```

`NodeSheet.tsx`:
```tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick, type NodeRow } from "../state";
import { route, closeNode } from "../router";
import { Sheet } from "../ui/Sheet";
import { Pill, Tier } from "../ui/Pill";
import { Segmented } from "../ui/Segmented";
import { Actions } from "./Actions";
import { Overview } from "./Overview";
import { DiffView } from "./DiffView";
import { Logs } from "./Logs";

export type NodeDetail = {
  node: NodeRow & { body_md: string | null; criteria_json: string | null; criteria_mode: string; summary: string | null; block_reason: string | null };
  notes: { kind: string; pinned: number | boolean; created_at: string; text: string }[];
  commits: { sha: string }[];
  predicted_touches: string[];
  verification: string;
};

export function NodeSheet() {
  const id = Number(route.value.query.get("node"));
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seg, setSeg] = useState("overview");
  // The sheet re-fetches on every SSE tick instead of closing under the reader.
  useEffect(() => { api<NodeDetail>(routes.node(id)).then((d) => { setDetail(d); setError(null); }, (e) => setError(e.message)); }, [id, refreshTick.value]);
  const title = detail ? `#${detail.node.id} ${detail.node.title}` : `#${id}`;
  return (
    <Sheet title={title} onClose={closeNode}>
      {error ? <div class="callout danger">{error}</div> : !detail ? <p class="muted">loading…</p> : (
        <div class="stack">
          <div class="row">
            <Pill status={detail.node.status} />
            <Tier tier={detail.node.risk_tier} />
            <span class="caption">{detail.node.kind}{detail.node.owner ? ` · owner ${detail.node.owner}` : ""}{detail.node.parent_id ? ` · parent #${detail.node.parent_id}` : ""}</span>
          </div>
          {detail.node.block_reason ? <div class="callout danger">blocked: {detail.node.block_reason}</div> : null}
          <Actions detail={detail} onDone={closeNode} />
          <Segmented options={[{ value: "overview", label: "Overview" }, { value: "diff", label: "Diff" }, { value: "logs", label: "Logs" }]} value={seg} onChange={setSeg} />
          {seg === "overview" ? <Overview detail={detail} /> : seg === "diff" ? <DiffView nodeId={id} /> : <Logs nodeId={id} />}
        </div>
      )}
    </Sheet>
  );
}
```

Add to `base.css`: `ul.criteria { margin: 4px 0; padding-left: 20px; } pre.logs { max-height: 50dvh; overflow: auto; }`.

- [ ] **Step 6: Wire into app.tsx** — delete `NodeSheetPlaceholder`, import `NodeSheet`, render `{nodeId ? <NodeSheet /> : null}`.

- [ ] **Step 7: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 8: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: task sheet with actions first, then overview, diff and logs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 10: Spec page with line comments

**Files:**
- Create: `dashboard/src/spec/SpecPage.tsx`, `dashboard/src/spec/spec.css`
- Modify: `dashboard/src/app.tsx` (`PAGES.spec = SpecPage`), `dashboard/src/main.tsx` (import css)
- Test: `dashboard/test/SpecPage.test.tsx`

**Interfaces:**
- Consumes: `GET /nodes/{id}` (as `NodeDetail`), `POST /nodes/{id}/comment {text, line?}`. Feedback notes with text `[L<n>] <comment>` anchor to line n; others are general. `route.value.params[0]` is the spec id.

- [ ] **Step 1: Write SpecPage.test.tsx**

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { SpecPage } from "../src/spec/SpecPage";
import { authed } from "../src/state";
import { resetRouteFromLocation } from "../src/router";

beforeEach(() => {
  authed.value = true;
  window.location.hash = "#/spec/3";
  resetRouteFromLocation();
});
afterEach(() => vi.unstubAllGlobals());

function stub() {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    const body = url === "/nodes/3/comment" ? { ok: 1 } : {
      node: { id: 3, project_id: 1, parent_id: null, kind: "spec", title: "Spec", status: "ready", risk_tier: "low", owner: null, body_md: "first\nsecond", criteria_json: "[]", criteria_mode: "manual", summary: null, block_reason: null },
      notes: [{ kind: "feedback", pinned: 0, created_at: "t", text: "[L2] tighten this" }, { kind: "feedback", pinned: 0, created_at: "t", text: "overall fine" }],
      commits: [], predicted_touches: [], verification: "human",
    };
    return { ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => "" };
  }));
  return calls;
}

test("line comments render under their line and general ones at the bottom", async () => {
  stub();
  render(<SpecPage />);
  expect(await screen.findByText("tighten this")).toBeInTheDocument();
  expect(screen.getByText("overall fine")).toBeInTheDocument();
});

test("tapping a line opens a composer that posts with the line number", async () => {
  const calls = stub();
  render(<SpecPage />);
  fireEvent.click(await screen.findByText("first"));
  const input = screen.getByPlaceholderText("comment on line 1");
  fireEvent.input(input, { target: { value: "why?" } });
  fireEvent.click(screen.getByText("Comment"));
  await waitFor(() => expect(calls.some((c) => c.url === "/nodes/3/comment" && c.body === '{"text":"why?","line":1}')).toBe(true));
});
```

- [ ] **Step 2: Run** — `npm test -- SpecPage`. Expected: FAIL.

- [ ] **Step 3: Write SpecPage.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, refreshTick, refresh, toast, toastError } from "../state";
import { route, navigate } from "../router";
import { Pill } from "../ui/Pill";
import { Button } from "../ui/Button";
import { Actions } from "../node/Actions";
import type { NodeDetail } from "../node/NodeSheet";

export function SpecPage() {
  const id = Number(route.value.params[0]);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openLine, setOpenLine] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [general, setGeneral] = useState("");

  useEffect(() => { api<NodeDetail>(routes.node(id)).then((d) => { setDetail(d); setError(null); }, (e) => setError(e.message)); }, [id, refreshTick.value]);

  async function send(text: string, line?: number) {
    if (!text.trim()) return;
    try {
      await post(routes.nodeComment(id), line === undefined ? { text } : { text, line });
      toast("comment saved"); setDraft(""); setGeneral(""); setOpenLine(null); refresh();
    } catch (e) { toastError(e); }
  }

  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!detail) return <div class="page"><p class="muted">loading…</p></div>;
  const byLine: Record<number, string[]> = {};
  const generalNotes: string[] = [];
  for (const note of detail.notes.filter((n) => n.kind === "feedback")) {
    const m = note.text.match(/^\[L(\d+)\] ([\s\S]*)$/);
    if (m) (byLine[Number(m[1])] ??= []).push(m[2]!); else generalNotes.push(note.text);
  }
  const lines = (detail.node.body_md || "").split("\n");
  return (
    <div class="page stack">
      <Button variant="plain" onClick={() => navigate("#/plan")}>‹ Plan</Button>
      <div class="row between"><h1 class="page-title">#{detail.node.id} {detail.node.title}</h1><Pill status={detail.node.status} /></div>
      <Actions detail={detail} onDone={() => navigate("#/plan")} />
      <div class="caption">Tap a line to comment on it. The agent sees comments in its brief.</div>
      <div class="card spec-body">
        {lines.map((text, i) => {
          const n = i + 1;
          return (
            <>
              <button type="button" class="spec-line" onClick={() => setOpenLine(openLine === n ? null : n)}>
                <span class="ln">{n}</span><span class="tx">{text || " "}</span>
              </button>
              {(byLine[n] ?? []).map((c) => <div class="callout spec-comment">{c}</div>)}
              {openLine === n && authed.value ? (
                <form class="spec-form row" onSubmit={(e) => { e.preventDefault(); void send(draft, n); }}>
                  <input placeholder={`comment on line ${n}`} value={draft} onInput={(e) => setDraft((e.target as HTMLInputElement).value)} />
                  <Button type="submit" variant="filled">Comment</Button>
                </form>
              ) : null}
            </>
          );
        })}
      </div>
      {generalNotes.length ? <section class="stack tight"><h3>General comments</h3>{generalNotes.map((c) => <div class="callout">{c}</div>)}</section> : null}
      {authed.value ? (
        <form class="stack tight" onSubmit={(e) => { e.preventDefault(); void send(general); }}>
          <textarea placeholder="general comment on this spec" value={general} onInput={(e) => setGeneral((e.target as HTMLTextAreaElement).value)} />
          <div class="actions"><Button type="submit">Comment</Button></div>
        </form>
      ) : null}
    </div>
  );
}
```

`spec.css`:
```css
.spec-body { padding: 8px 0; }
.spec-line { display: flex; gap: 10px; width: 100%; text-align: left; background: none; border: 0; padding: 2px 12px; font-family: var(--mono); font-size: 13px; cursor: pointer; color: var(--text); }
.spec-line:hover { background: var(--surface-2); }
.spec-line .ln { color: var(--text-2); min-width: 28px; text-align: right; user-select: none; }
.spec-line .tx { white-space: pre-wrap; flex: 1; }
.spec-comment { margin: 4px 12px 8px 50px; }
.spec-form { margin: 4px 12px 8px 50px; flex-wrap: nowrap; }
.spec-form input { flex: 1; }
```

- [ ] **Step 4: Register** `spec: SpecPage` in `PAGES`; import the css in `main.tsx`.

- [ ] **Step 5: Run** — `npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: spec page with line-anchored comments

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 11: Inbox page

**Files:**
- Create: `dashboard/src/inbox/InboxPage.tsx`
- Modify: `dashboard/src/app.tsx` (`PAGES.inbox`), `dashboard/src/main.tsx` (poll the badge)
- Test: `dashboard/test/InboxPage.test.tsx`

**Interfaces:**
- Consumes: `GET /inbox` → `{questions: {id, node_id, text, default_answer, default_ok}[], review: NodeRow[], unverified_external: {node_id, title}[], awaiting_approval: NodeRow[], blocked: NodeRow[], structure_updates: Ev[], audit_items: Ev[], signals: Ev[], unattributed_commits: Ev[]}` where `Ev = {id, ts, node_id, payload: string}`; `POST /questions/{id}/answer {text}`; `POST /events/{id}/ack`.
- Produces: `countInbox(data): number`, `InboxPage()`.

- [ ] **Step 1: Write InboxPage.test.tsx**

```tsx
import { render, screen } from "@testing-library/preact";
import { InboxPage, countInbox } from "../src/inbox/InboxPage";
import { authed, inboxCount } from "../src/state";

const empty = { questions: [], review: [], unverified_external: [], awaiting_approval: [], blocked: [], structure_updates: [], audit_items: [], signals: [], unattributed_commits: [] };

function stub(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => data, text: async () => "" })));
}
beforeEach(() => { authed.value = true; });
afterEach(() => vi.unstubAllGlobals());

test("an empty inbox says so", async () => {
  stub(empty);
  render(<InboxPage />);
  expect(await screen.findByText("Nothing waiting on you.")).toBeInTheDocument();
});

test("the count is the sum of every list and the badge follows", async () => {
  const data = { ...empty, questions: [{ id: 1, node_id: 2, text: "which db?", default_answer: "sqlite", default_ok: true }], review: [{ id: 2, project_id: 1, parent_id: 1, kind: "task", title: "x", status: "review", risk_tier: "high", owner: null }], signals: [{ id: 9, ts: "t", node_id: null, payload: '{"sha":"abcdef123456","files":["a.py"]}' }] };
  expect(countInbox(data)).toBe(3);
  stub(data);
  render(<InboxPage />);
  expect(await screen.findByText("which db?")).toBeInTheDocument();
  expect(inboxCount.value).toBe(3);
});
```

- [ ] **Step 2: Run** — `npm test -- InboxPage`. Expected: FAIL.

- [ ] **Step 3: Write InboxPage.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, inboxCount, refreshTick, refresh, toast, toastError, type NodeRow } from "../state";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";
import { NodeRowItem } from "../ui/NodeRowItem";
import { Tier } from "../ui/Pill";
import { classifyDiffLine } from "../node/diff";

type Ev = { id: number; ts: string; node_id: number | null; payload: string };
type Question = { id: number; node_id: number; text: string; default_answer: string | null; default_ok: boolean };
export type Inbox = {
  questions: Question[]; review: NodeRow[]; unverified_external: { node_id: number; title: string }[];
  awaiting_approval: NodeRow[]; blocked: NodeRow[]; structure_updates: Ev[]; audit_items: Ev[]; signals: Ev[]; unattributed_commits: Ev[];
};

export function countInbox(d: Inbox): number {
  return d.questions.length + d.review.length + d.awaiting_approval.length + d.blocked.length + d.structure_updates.length + d.audit_items.length + d.signals.length + d.unattributed_commits.length;
}

function payloadOf(ev: Ev): Record<string, unknown> {
  try { return JSON.parse(ev.payload || "{}"); } catch { return {}; }
}

function Section({ title, count, children }: { title: string; count: number; children: preact.ComponentChildren }) {
  if (!count) return null;
  return <section class="stack tight"><h2>{title} <span class="caption">{count}</span></h2>{children}</section>;
}

function AckCard({ ev, summary, children }: { ev: Ev; summary: string; children?: preact.ComponentChildren }) {
  async function ack() { try { await post(routes.eventAck(ev.id)); toast("acknowledged"); refresh(); } catch (e) { toastError(e); } }
  return (
    <div class="card stack tight">
      <div class="row between"><div>{summary}</div>{authed.value ? <Button onClick={ack}>Ack</Button> : null}</div>
      <div class="caption">{ev.ts}{ev.node_id ? ` · task #${ev.node_id}` : ""}</div>
      {children}
    </div>
  );
}

function QuestionCard({ q }: { q: Question }) {
  const [answer, setAnswer] = useState("");
  async function send() {
    if (!answer.trim()) return;
    try { await post(routes.questionAnswer(q.id), { text: answer }); toast("answered"); refresh(); } catch (e) { toastError(e); }
  }
  return (
    <form class="card stack tight" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <div>{q.text}</div>
      <div class="caption">task #{q.node_id} · default: {q.default_answer || "none"}{q.default_ok ? " (safe to default)" : ""}</div>
      {authed.value ? <div class="row" style={{ flexWrap: "nowrap" }}><input placeholder="answer" value={answer} onInput={(e) => setAnswer((e.target as HTMLInputElement).value)} /><Button type="submit" variant="filled">Answer</Button></div> : null}
    </form>
  );
}

export function InboxPage() {
  const [data, setData] = useState<Inbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Inbox>(routes.inbox()).then((d) => { setData(d); setError(null); inboxCount.value = countInbox(d); }, (e) => setError(e.message)); }, [refreshTick.value]);
  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!data) return <div class="page"><p class="muted">loading…</p></div>;
  const rowOf = (n: { node_id: number; title: string }): NodeRow => ({ id: n.node_id, title: n.title, project_id: 0, parent_id: null, kind: "task", status: "review", risk_tier: "low", owner: null });
  return (
    <div class="page stack">
      <h1 class="page-title">Inbox</h1>
      {countInbox(data) === 0 ? <Empty text="Nothing waiting on you." check /> : null}
      <Section title="Questions" count={data.questions.length}>{data.questions.map((q) => <QuestionCard q={q} />)}</Section>
      <Section title="Awaiting review" count={data.review.length}><div class="list">{data.review.map((n) => <NodeRowItem node={n} right={<Tier tier={n.risk_tier} />} />)}</div></Section>
      <Section title="Unverified external criteria" count={data.unverified_external.length}>
        <div class="list">{data.unverified_external.map((u) => <NodeRowItem node={rowOf(u)} sub="muvue could not run these checks; verify before approving" />)}</div>
      </Section>
      <Section title="Criteria changed, awaiting approval" count={data.awaiting_approval.length}><div class="list">{data.awaiting_approval.map((n) => <NodeRowItem node={n} />)}</div></Section>
      <Section title="Blocked" count={data.blocked.length}><div class="list">{data.blocked.map((n) => <NodeRowItem node={n} sub={n.block_reason || undefined} />)}</div></Section>
      <Section title="Structure updates" count={data.structure_updates.length}>
        {data.structure_updates.map((ev) => { const p = payloadOf(ev); return (
          <AckCard ev={ev} summary={`structure diff on ${p.ref} (${String(p.sha || "").slice(0, 12)})`}>
            {p.message ? <div class="muted">{String(p.message)}</div> : null}
            {p.pr_url ? <a href={String(p.pr_url)} target="_blank" rel="noopener">{String(p.pr_url)}</a> : null}
            {p.pr_error ? <div class="muted">PR not opened: {String(p.pr_error)}</div> : null}
          </AckCard>); })}
      </Section>
      <Section title="Audit drafts" count={data.audit_items.length}>
        {data.audit_items.map((ev) => { const p = payloadOf(ev); return (
          <AckCard ev={ev} summary={`component #${p.component_id} ${p.name || ""}: ${p.message || "re-verify"}`}>
            <details><summary class="caption">diff</summary><pre class="scroll-x">{String(p.diff || "").split("\n").map((l) => <span class={classifyDiffLine(l)}>{l + "\n"}</span>)}</pre></details>
          </AckCard>); })}
      </Section>
      <Section title="Commits touching components without a task" count={data.signals.length}>
        {data.signals.map((ev) => { const p = payloadOf(ev); return <AckCard ev={ev} summary={`${String(p.sha || "").slice(0, 12)} touched ${((p.files as string[]) || []).join(", ")}`} />; })}
      </Section>
      <Section title="Unattributed commits" count={data.unattributed_commits.length}>
        {data.unattributed_commits.map((ev) => { const p = payloadOf(ev); return <AckCard ev={ev} summary={`${String(p.sha || "").slice(0, 12)} has no resolvable Muvue-Node trailer`}><div class="muted">{((p.files as string[]) || []).join(", ")}</div></AckCard>; })}
      </Section>
    </div>
  );
}
```

`preact.ComponentChildren` needs `import type * as preact from "preact";` at the top, or import `ComponentChildren` directly; use the direct import.

- [ ] **Step 4: Badge on every page** — in `main.tsx`'s `effect`, after `loadProjects()`, also `api<Inbox>(routes.inbox()).then((d) => { inboxCount.value = countInbox(d); }, toastError)`. Import `countInbox` from `./inbox/InboxPage` and `inboxCount` from `./state`.

- [ ] **Step 5: Register** `inbox: InboxPage`. Run `npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: inbox with inline answer and ack actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 12: Activity page — timeline and revisions

**Files:**
- Create: `dashboard/src/activity/ActivityPage.tsx`, `Timeline.tsx`, `Revisions.tsx`
- Modify: `dashboard/src/app.tsx` (`PAGES.activity`)
- Test: `dashboard/test/Timeline.test.tsx`

**Interfaces:**
- Consumes: `GET /events?limit=100&project_id=` → `{id, ts, type, actor, actor_evidence, node_id}[]` (oldest first); `GET /projects/{id}/revisions` → `{n, approved_at, diff}[]`; `POST /nodes/{project_id}/approve {target: "revision", n}`.
- Produces: `familyOf(type): "task"|"review"|"runner"|"commit"|"question"|"project"|"other"` (prefix before the first dot), `groupByDay(events)`.

- [ ] **Step 1: Write Timeline.test.tsx**

```tsx
import { familyOf, groupByDay } from "../src/activity/Timeline";

test("event families come from the type prefix", () => {
  expect(familyOf("node.done")).toBe("task");
  expect(familyOf("review.awaiting")).toBe("review");
  expect(familyOf("runner.rate_limited")).toBe("runner");
  expect(familyOf("commit.linked")).toBe("commit");
  expect(familyOf("question.asked")).toBe("question");
  expect(familyOf("project.phase_changed")).toBe("project");
  expect(familyOf("weird")).toBe("other");
});

test("events group by day, newest day first, newest event first within a day", () => {
  const evs = [
    { id: 1, ts: "2026-09-26T10:00:00", type: "node.created", actor: "human", actor_evidence: "tty", node_id: 1 },
    { id: 2, ts: "2026-09-27T09:00:00", type: "node.start", actor: "agent", actor_evidence: "x", node_id: 1 },
    { id: 3, ts: "2026-09-27T11:00:00", type: "node.done", actor: "agent", actor_evidence: "x", node_id: 1 },
  ];
  const groups = groupByDay(evs);
  expect(groups.map((g) => g.day)).toEqual(["2026-09-27", "2026-09-26"]);
  expect(groups[0]!.events.map((e) => e.id)).toEqual([3, 2]);
});
```

- [ ] **Step 2: Run** — `npm test -- Timeline`. Expected: FAIL.

- [ ] **Step 3: Write Timeline.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { projectId, refreshTick } from "../state";
import { openNode } from "../router";
import { Empty } from "../ui/Empty";

export type Ev = { id: number; ts: string; type: string; actor: string; actor_evidence: string; node_id: number | null };
const FAMILIES = ["task", "review", "runner", "commit", "question", "project"] as const;
type Family = (typeof FAMILIES)[number] | "other";
const PREFIX: Record<string, Family> = { node: "task", review: "review", runner: "runner", commit: "commit", question: "question", project: "project" };

export function familyOf(type: string): Family {
  return PREFIX[type.split(".")[0] ?? ""] ?? "other";
}

export function groupByDay(events: Ev[]): { day: string; events: Ev[] }[] {
  const byDay = new Map<string, Ev[]>();
  for (const ev of [...events].sort((a, b) => b.ts.localeCompare(a.ts) || b.id - a.id)) {
    const day = ev.ts.slice(0, 10);
    (byDay.get(day) ?? byDay.set(day, []).get(day)!).push(ev);
  }
  return [...byDay.entries()].map(([day, evs]) => ({ day, events: evs }));
}

const CHIPS: { value: Family | "all"; label: string }[] = [
  { value: "all", label: "All" }, { value: "task", label: "Tasks" }, { value: "review", label: "Reviews" }, { value: "runner", label: "Runner" },
  { value: "commit", label: "Commits" }, { value: "question", label: "Questions" }, { value: "project", label: "Project" },
];

export function Timeline() {
  const [events, setEvents] = useState<Ev[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chip, setChip] = useState<Family | "all">("all");
  useEffect(() => { api<Ev[]>(routes.events(projectId.value)).then((e) => { setEvents(e); setError(null); }, (e) => setError(e.message)); }, [projectId.value, refreshTick.value]);
  if (error) return <div class="callout danger">{error}</div>;
  if (!events) return <p class="muted">loading…</p>;
  const shown = chip === "all" ? events : events.filter((e) => familyOf(e.type) === chip);
  return (
    <div class="stack">
      <div class="chips">{CHIPS.map((c) => <button type="button" class={"chip" + (chip === c.value ? " on" : "")} onClick={() => setChip(c.value)}>{c.label}</button>)}</div>
      {shown.length ? groupByDay(shown).map((g) => (
        <section>
          <h3 class="group-head sticky">{g.day}</h3>
          <div class="list">
            {g.events.map((ev) => (
              <button type="button" class="list-row" onClick={() => { if (ev.node_id) openNode(ev.node_id); }} disabled={!ev.node_id}>
                <span class="grow">
                  <span class="title">{ev.type}{ev.node_id ? ` · #${ev.node_id}` : ""}</span>
                  <span class="caption" style={{ display: "block" }}>{ev.actor} ({ev.actor_evidence}) · {ev.ts.slice(11, 19)}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )) : <Empty text="No events yet." />}
    </div>
  );
}
```

Add to `base.css`:
```css
.chips { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
.chip { border: 1px solid var(--border); background: var(--surface); border-radius: 999px; padding: 6px 12px; font-size: 13px; cursor: pointer; white-space: nowrap; min-height: 36px; }
.chip.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.group-head.sticky { position: sticky; top: 0; background: var(--bg); padding: 6px 0; z-index: 1; }
.list-row:disabled { cursor: default; }
.list-row:disabled:hover { background: none; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; font-size: 13px; }
.kv dt { color: var(--text-2); margin: 0; } .kv dd { margin: 0; font-family: var(--mono); word-break: break-word; }
```

- [ ] **Step 4: Write Revisions.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { authed, projectId, refreshTick, refresh, toast, toastError } from "../state";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";

type Rev = { n: number; approved_at: string | null; diff: unknown };

function KeyValues({ value }: { value: unknown }) {
  if (typeof value !== "object" || value === null) return <pre>{JSON.stringify(value)}</pre>;
  return (
    <dl class="kv">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <><dt>{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v, null, 1) : String(v)}</dd></>
      ))}
    </dl>
  );
}

export function Revisions() {
  const [revs, setRevs] = useState<Rev[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pid = projectId.value;
  useEffect(() => {
    if (pid === null) { setRevs([]); return; }
    api<Rev[]>(routes.revisions(pid)).then((r) => { setRevs(r); setError(null); }, (e) => setError(e.message));
  }, [pid, refreshTick.value]);
  async function approve(n: number) {
    if (pid === null) return;
    try { await post(routes.nodeApprove(pid), { target: "revision", n }); toast(`revision ${n} approved`); refresh(); } catch (e) { toastError(e); }
  }
  if (error) return <div class="callout danger">{error}</div>;
  if (!revs) return <p class="muted">loading…</p>;
  if (!revs.length) return <Empty text="No plan revisions yet." />;
  return (
    <div class="stack">
      {revs.map((rev) => (
        <div class="card stack tight">
          <div class="row between"><h3>Revision {rev.n}</h3><span class="caption">{rev.approved_at ? `approved ${rev.approved_at}` : "pending"}</span></div>
          <KeyValues value={rev.diff} />
          {!rev.approved_at && authed.value ? <div class="actions"><Button variant="filled" onClick={() => approve(rev.n)}>Approve revision</Button></div> : null}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Write ActivityPage.tsx**

```tsx
import { route, navigate } from "../router";
import { Segmented } from "../ui/Segmented";
import { Timeline } from "./Timeline";
import { Revisions } from "./Revisions";

export function ActivityPage() {
  const seg = route.value.params[0] === "revisions" ? "revisions" : "timeline";
  return (
    <div class="page stack">
      <div class="row between">
        <h1 class="page-title">Activity</h1>
        <Segmented options={[{ value: "timeline", label: "Timeline" }, { value: "revisions", label: "Revisions" }]} value={seg} onChange={(v) => navigate(v === "revisions" ? "#/activity/revisions" : "#/activity")} />
      </div>
      {seg === "timeline" ? <Timeline /> : <Revisions />}
    </div>
  );
}
```

- [ ] **Step 6: Register** `activity: ActivityPage`. Run `npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 7: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: activity page with day-grouped timeline and revisions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 13: Spend page

**Files:**
- Create: `dashboard/src/spend/SpendPage.tsx`
- Modify: `dashboard/src/app.tsx` (`PAGES.spend`)
- Test: `dashboard/test/SpendPage.test.tsx`

**Interfaces:**
- Consumes: `GET /kpis` → `{drift_pct, touch_drift, rubber_stamp_rate, rubber_stamps, approvals_timed, tokens_per_node, spend_vs_budget, spend_by_driver: Record<string, {spent, limit, unit, pct, warn, exhausted}>}`.

- [ ] **Step 1: Write SpendPage.test.tsx**

```tsx
import { render, screen } from "@testing-library/preact";
import { SpendPage } from "../src/spend/SpendPage";

afterEach(() => vi.unstubAllGlobals());

test("KPIs and per-agent bars render", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), text: async () => "",
    json: async () => ({ drift_pct: 0.5, touch_drift: 0.125, rubber_stamp_rate: 0, rubber_stamps: 0, approvals_timed: 4, tokens_per_node: 1234.6, spend_vs_budget: 0.3,
      spend_by_driver: { claude: { spent: 3, limit: 10, unit: "usd", pct: 0.3, warn: false, exhausted: false } } }) })));
  render(<SpendPage />);
  expect(await screen.findByText("12.5%")).toBeInTheDocument();
  expect(screen.getByText("1235")).toBeInTheDocument();
  expect(screen.getByText("claude")).toBeInTheDocument();
  expect(screen.getByText("3 / 10 usd")).toBeInTheDocument();
});
```

- [ ] **Step 2: Write SpendPage.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refreshTick } from "../state";
import { Empty } from "../ui/Empty";

type Driver = { spent: number; limit: number; unit: string; pct: number; warn: boolean; exhausted: boolean };
type Kpis = { drift_pct: number; touch_drift: number; rubber_stamp_rate: number; rubber_stamps: number; approvals_timed: number; tokens_per_node: number; spend_vs_budget: number; spend_by_driver: Record<string, Driver> };

const pct = (x: number) => (x * 100).toFixed(1) + "%";

export function SpendPage() {
  const [k, setK] = useState<Kpis | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Kpis>(routes.kpis()).then((d) => { setK(d); setError(null); }, (e) => setError(e.message)); }, [refreshTick.value]);
  if (error) return <div class="page"><div class="callout danger">{error}</div></div>;
  if (!k) return <div class="page"><p class="muted">loading…</p></div>;
  const cards: [string, string, string?][] = [
    ["Components verified in the last 50 commits", pct(k.drift_pct)],
    ["Touches outside the prediction", pct(k.touch_drift)],
    ["Rubber-stamp rate (medium/high)", pct(k.rubber_stamp_rate), `${k.rubber_stamps} fast of ${k.approvals_timed} timed approvals`],
    ["Tokens per task", String(Math.round(k.tokens_per_node))],
    ["Worst agent spend vs budget", pct(k.spend_vs_budget)],
  ];
  const drivers = Object.entries(k.spend_by_driver || {});
  return (
    <div class="page stack">
      <h1 class="page-title">Spend</h1>
      <div class="grid-kpi">
        {cards.map(([label, value, sub]) => <div class="card kpi"><div class="kpi-value">{value}</div><div class="caption">{label}</div>{sub ? <div class="caption">{sub}</div> : null}</div>)}
      </div>
      <h2>Spend per agent</h2>
      {drivers.length ? drivers.map(([name, d]) => (
        <div class="card stack tight">
          <div class="row between"><strong>{name}</strong><span class="caption">{d.spent} / {d.limit} {d.unit}</span></div>
          <div class={"progress" + (d.exhausted ? " exhausted" : d.warn ? " warn" : "")}><span style={{ width: Math.min(100, d.pct * 100) + "%" }} /></div>
          <div class="caption">{pct(d.pct)}{d.exhausted ? " · exhausted" : d.warn ? " · warning" : ""}</div>
        </div>
      )) : <Empty text="No agent has a [agents.<name>.budget]." />}
    </div>
  );
}
```

Add to `base.css`: `.kpi { text-align: center; } .kpi-value { font-size: 28px; font-weight: 600; }`.

- [ ] **Step 3: Register** `spend: SpendPage`. Run `npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 4: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: spend page with KPI cards and per-agent budget bars

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 14: Close-project sheet and command palette

**Files:**
- Create: `dashboard/src/project/CloseSheet.tsx` (replace the Task 7 stub), `dashboard/src/shell/CommandPalette.tsx`
- Modify: `dashboard/src/app.tsx` (render the palette when `palette` is true)
- Test: `dashboard/test/CommandPalette.test.tsx`

**Interfaces:**
- Consumes: `GET /projects/{id}/close-preview` → `{closeable: boolean, blocking_nodes: number[], diff: {decisions: {title}[], promoted_lessons: {title}[], components: {name}[], changed_components: {name}[]}}`; `POST /projects/{id}/close {pr}` → `{result?: {fast_forwarded, structure_ref, pr_url?, pr_error?}}` (or the same fields at top level); `GET /nodes?project_id=` → `NodeRow[]`.
- Produces: `CloseSheet({projectId, onClose})`, `CommandPalette({onClose})`, `paletteItems(query, nodes, projects, actions): Item[]`.

- [ ] **Step 1: Write CloseSheet.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { refresh, toast, toastError } from "../state";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";

type Preview = { closeable: boolean; blocking_nodes: number[]; diff: Record<string, { title?: string; name?: string }[]> };
type CloseResult = { fast_forwarded?: boolean; structure_ref?: string; pr_url?: string; pr_error?: string };
const SECTIONS: [string, string][] = [["decisions", "Decisions"], ["promoted_lessons", "Promoted lessons"], ["components", "Components"], ["changed_components", "Changed components"]];

export function CloseSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pr, setPr] = useState(false);
  useEffect(() => { api<Preview>(routes.closePreview(projectId)).then(setPreview, (e) => setError(e.message)); }, [projectId]);
  async function close() {
    try {
      const r = await post<{ result?: CloseResult } & CloseResult>(routes.projectClose(projectId), { pr });
      const res = r.result ?? r;
      toast(res.fast_forwarded ? "closed; main fast-forwarded" : `closed; structure is on ${res.structure_ref}${res.pr_url ? `, PR ${res.pr_url}` : res.pr_error ? ` (PR failed: ${res.pr_error})` : ""}`);
      refresh(); onClose();
    } catch (e) { toastError(e); }
  }
  return (
    <Sheet title={`Close project #${projectId}`} onClose={onClose}>
      {error ? <div class="callout danger">{error}</div> : !preview ? <p class="muted">loading…</p> : (
        <div class="stack">
          {!preview.closeable ? <div class="callout danger">Not closeable yet: tasks {preview.blocking_nodes.map((n) => "#" + n).join(", ")} are not done.</div> : null}
          {SECTIONS.map(([key, label]) => {
            const items = preview.diff[key] ?? [];
            return <section><h3>{label} <span class="caption">{items.length}</span></h3>{items.length ? <ul class="criteria">{items.map((i) => <li>{i.title ?? i.name ?? JSON.stringify(i)}</li>)}</ul> : <p class="muted">none</p>}</section>;
          })}
          <label class="row"><input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={pr} onChange={(e) => setPr((e.target as HTMLInputElement).checked)} /> open a GitHub PR if main can't be fast-forwarded</label>
          <div class="actions"><Button variant="danger" disabled={!preview.closeable} onClick={close}>Close and commit structure</Button><Button variant="plain" onClick={onClose}>Cancel</Button></div>
        </div>
      )}
    </Sheet>
  );
}
```

- [ ] **Step 2: Write CommandPalette.test.tsx**

```tsx
import { paletteItems } from "../src/shell/CommandPalette";

const nodes = [
  { id: 7, project_id: 1, parent_id: null, kind: "task", title: "Pitch detection", status: "ready" as const, risk_tier: "low" as const, owner: null },
  { id: 8, project_id: 1, parent_id: null, kind: "task", title: "MIDI export", status: "done" as const, risk_tier: "low" as const, owner: null },
];
const projects = [{ id: 1, goal: "voxscore", phase: "planning" as const }, { id: 2, goal: "other", phase: "closed" as const }];
const actions = [{ label: "Pause", run: () => {} }];

test("matches tasks by number and by title, case-insensitive", () => {
  expect(paletteItems("7", nodes, projects, actions).map((i) => i.label)).toEqual(["#7 Pitch detection"]);
  expect(paletteItems("midi", nodes, projects, actions).map((i) => i.label)).toEqual(["#8 MIDI export"]);
});

test("empty query lists actions first, then tasks, then projects", () => {
  const labels = paletteItems("", nodes, projects, actions).map((i) => i.label);
  expect(labels[0]).toBe("Pause");
  expect(labels).toContain("#7 Pitch detection");
  expect(labels).toContain("Switch to #2 other");
});
```

- [ ] **Step 3: Write CommandPalette.tsx**

```tsx
import { useEffect, useState } from "preact/hooks";
import { api, post } from "../api/client";
import { routes } from "../api/routes";
import { projects, projectId, currentProject, authed, refresh, toast, toastError, type NodeRow, type Project } from "../state";
import { openNode, navigate } from "../router";
import { Sheet } from "../ui/Sheet";

export type Item = { label: string; hint?: string; run: () => void };
type Action = { label: string; run: () => void };

export function paletteItems(query: string, nodes: NodeRow[], projs: Project[], actions: Action[]): Item[] {
  const q = query.trim().toLowerCase();
  const hit = (s: string) => !q || s.toLowerCase().includes(q);
  const items: Item[] = [];
  for (const a of actions) if (hit(a.label)) items.push({ label: a.label, hint: "action", run: a.run });
  for (const n of nodes) {
    const label = `#${n.id} ${n.title}`;
    if (!q || String(n.id) === q || hit(n.title)) items.push({ label, hint: n.status.replace(/_/g, " "), run: () => openNode(n.id) });
  }
  for (const p of projs) {
    const label = `Switch to #${p.id} ${p.goal}`;
    if (hit(label)) items.push({ label, hint: p.phase, run: () => { projectId.value = p.id; refresh(); navigate("#/plan"); } });
  }
  return items;
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [cursor, setCursor] = useState(0);
  useEffect(() => { api<NodeRow[]>(routes.nodes(projectId.value)).then(setNodes, toastError); }, []);
  const p = currentProject.value;
  const actions: Action[] = [];
  if (authed.value && p) {
    const act = (label: string, path: string, body?: unknown) => actions.push({ label, run: () => { post(path, body).then(() => { toast(label.toLowerCase() + ": done"); refresh(); }, toastError); onClose(); } });
    if (p.phase !== "paused" && p.phase !== "closed") act("Pause", routes.projectPause(p.id));
    if (p.phase === "paused") act("Resume", routes.projectResume(p.id));
    const spec = nodes.find((n) => n.kind === "spec" && n.status === "pending");
    if (spec) act("Approve spec", routes.nodeApprove(spec.id), { target: "spec" });
    if (p.phase === "planning" && !spec && nodes.some((n) => n.kind !== "spec")) act("Approve task list", routes.nodeApprove(p.id), { target: "gate2" });
  }
  const items = paletteItems(query, nodes, projects.value, actions).slice(0, 20);
  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(items.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const it = items[cursor]; if (it) { it.run(); onClose(); } }
  }
  return (
    <Sheet title="Jump to" onClose={onClose}>
      <div class="stack tight">
        <input autofocus placeholder="task number, title, project or action" value={query} onInput={(e) => { setQuery((e.target as HTMLInputElement).value); setCursor(0); }} onKeyDown={onKey} />
        <div class="list">
          {items.map((it, i) => (
            <button type="button" class={"list-row" + (i === cursor ? " on" : "")} onClick={() => { it.run(); onClose(); }}>
              <span class="grow title">{it.label}</span>{it.hint ? <span class="caption">{it.hint}</span> : null}
            </button>
          ))}
          {!items.length ? <div class="list-row muted">no matches</div> : null}
        </div>
      </div>
    </Sheet>
  );
}
```

- [ ] **Step 4: Wire into app.tsx** — replace `{palette ? null : null}` with `{palette ? <CommandPalette onClose={() => setPalette(false)} /> : null}` and import it.

- [ ] **Step 5: Run** — `cd /home/eugene/projects/muvue/dashboard && npm test && npm run typecheck`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /home/eugene/projects/muvue && git add dashboard && git commit -m "Dashboard: close-project sheet and command palette

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 15: Swap the built page in — Python tests, CI, docs

**Files:**
- Modify: `tests/test_dashboard_static.py` (rewrite), `.github/workflows/ci.yml`, `README.md` (Dashboard and Development sections), `CHANGELOG.md` (`[Unreleased]`), `docs/decisions.md` (#166), `docs/threat-model.md` (one sentence), `src/muvue/api/static/index.html` (built output)

- [ ] **Step 1: Rewrite tests/test_dashboard_static.py**

```python
"""Static checks on the built dashboard (v4 section 8, working rules):
no CDN or external script, no inline event handlers in markup, no
persistent storage of the token, and every endpoint it calls exists.
The page is built from dashboard/ (decision #166); the API paths it can
call are the ones spelled in dashboard/src/api/routes.ts."""

from __future__ import annotations

import re
from pathlib import Path

from muvue.api import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "muvue" / "api" / "static" / "index.html"
ROUTES_TS = ROOT / "dashboard" / "src" / "api" / "routes.ts"


def _markup_only(html: str) -> str:
    """The page's script and style are inlined; only the markup around
    them can carry an event-handler attribute."""
    return re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)


def test_no_external_resources():
    html = INDEX.read_text()
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=\"https?:", html)
    assert "https://" not in _markup_only(html)


def test_no_inline_event_handlers():
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", _markup_only(INDEX.read_text()))


def test_token_never_reaches_persistent_storage():
    html = INDEX.read_text()
    for api in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert api not in html


def test_the_page_is_the_built_bundle():
    html = INDEX.read_text()
    assert '<script type="module"' in html
    assert "--accent: #D97757" in html


def _routes_from_ts() -> set[str]:
    text = ROUTES_TS.read_text()
    paths = set(re.findall(r'"(/[^"?]*)"', text))
    paths |= {re.sub(r"\$\{[^}]+\}", "0", p) for p in re.findall(r"`(/[^`?]*)`", text)}
    return paths


def test_every_route_in_routes_ts_is_served(tmp_path):
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    served = [r.path for r in create_app(tmp_path).routes]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in served]
    called = _routes_from_ts()
    assert len(called) >= 20, "found too few paths; the routes.ts pattern is stale"
    missing = sorted(c for c in called if not any(p.match(c) for p in patterns))
    assert missing == []
```

- [ ] **Step 2: Build the page for real and run the Python tests**

Run: `cd /home/eugene/projects/muvue/dashboard && npm run build && cd .. && uv run pytest -q tests/test_dashboard_static.py tests/test_api.py tests/test_w9_api.py tests/test_daemon_security.py`
Expected: all pass. If `test_no_inline_event_handlers` fails, find the offending markup with `grep -n -E '<[^>]+\son[a-z]+\s*=' src/muvue/api/static/index.html` after stripping scripts; it must be markup, not JS.

- [ ] **Step 3: CI job** — append to `.github/workflows/ci.yml`:

```yaml
  dashboard:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: dashboard
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build
      - name: The committed page must be the build of this source
        run: git diff --exit-code -- ../src/muvue/api/static/index.html
```

- [ ] **Step 4: Docs**

`docs/decisions.md`, append:
```markdown
166. **The dashboard is built with Preact; the build output is committed.**
    The hand-written single-file page had grown to 800 lines of DOM
    calls, dark-only, with a modal that phones could not scroll to the
    bottom of. It is now a Preact and TypeScript app in `dashboard/`,
    built by Vite into one self-contained `index.html` and copied to
    `src/muvue/api/static/`. The runtime contract does not change: the
    daemon serves one file, the page fetches nothing but the daemon's
    API, and the token never reaches web storage. Tests hold the
    contract on the built file, `routes.ts` is the one place an API
    path may be spelled, and CI fails when the committed page is not
    the build of the committed source. Python users never need node.
```

`docs/threat-model.md`: in the control that describes the dashboard page (search for "single-file" or `index.html`), add: "The page is built from `dashboard/` and committed; `tests/test_dashboard_static.py` checks the built file, not the source."

`README.md`:
- Dashboard section, replace the "The views:" list with:
  ```markdown
  The dashboard is organised around what you do next:
  - **Plan**: the spec with its approve button, then the tasks as a
    diagram (parent and dependency edges, coloured by status) or a list
    grouped by status. Tap a task for its sheet: actions first, then
    overview, diff and live logs.
  - **Inbox**: everything waiting on you, with the answer, approve and
    acknowledge buttons inline.
  - **Activity**: the timeline grouped by day, and plan revisions.
  - **Spend**: KPIs and each agent's spend against its budget.
  - The project menu (top left) switches projects and holds pause,
    resume and close. `⌘K` opens a jump-to palette.

  It follows the system light or dark setting and is laid out for a
  phone as well as a desktop.
  ```
- Development section, after the pytest lines:
  ```bash
  cd dashboard && npm ci && npm test && npm run build   # rebuilds src/muvue/api/static/index.html; commit it
  ```
  and a sentence: "The dashboard source is `dashboard/`; the built page is committed, and CI checks it matches."

`CHANGELOG.md`, under `## [Unreleased]` add before `### Fixed`:
```markdown
### Changed
- The dashboard is rebuilt as a Preact app (`dashboard/`), organised
  as Plan, Inbox, Activity and Spend, with a task sheet whose actions
  sit at the top, light and dark modes in the Claude palette, a phone
  layout with a tab bar, and a `⌘K` palette. The runtime contract is
  unchanged: one self-contained file, no network fetches beyond the
  daemon (decision #166).

### Added
- `GET /agents`: the names under `[agents.*]`, for the dashboard's
  start-with-agent picker.
```

- [ ] **Step 5: Full suite**

Run: `cd /home/eugene/projects/muvue && uv run pytest -q`
Expected: all pass (about 870). Report the exact count.

- [ ] **Step 6: Commit** (this is the commit that replaces the old page)

```bash
cd /home/eugene/projects/muvue && git add -A dashboard src/muvue/api/static/index.html tests/test_dashboard_static.py .github/workflows/ci.yml README.md CHANGELOG.md docs/decisions.md docs/threat-model.md && git commit -m "Replace the dashboard with the Preact build

The daemon now serves the page built from dashboard/: Plan, Inbox,
Activity and Spend, a task sheet with its actions at the top, light
and dark modes in the Claude palette, and a phone layout. The static
checks run on the built file and CI fails when it is stale.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 16: Verify in a real browser, phone and desktop, light and dark

**Files:**
- Create: `dashboard/e2e/screenshots.mjs` (a script, not a test; run by hand)

Playwright is available at `/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core` with the browser at `~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell` (memory `local-headless-browser`). SVG node text needs `click({ force: true })`.

- [ ] **Step 1: Scratch repo with a pending spec and three tasks**

```bash
rm -rf /tmp/muvue-ui-demo && mkdir -p /tmp/muvue-ui-demo && cd /tmp/muvue-ui-demo && git init -q && git config user.email t@t.com && git config user.name test && echo hi > README.md && git add -A && git commit -qm init
cd /home/eugene/projects/muvue && uv run muvue init /tmp/muvue-ui-demo && uv run muvue project create --goal "voxscore: voice to sheet music" --path /tmp/muvue-ui-demo && uv run muvue spec 1 --title "Voxscore" --body $'Record voice.\nDetect pitch.\nExport MusicXML.' --path /tmp/muvue-ui-demo
uv run muvue serve --path /tmp/muvue-ui-demo --port 8799 > /tmp/claude-1000/-home-eugene/f5f9b726-c9a6-477c-8138-ceb693c7b085/scratchpad/ui-demo-serve.log 2>&1 &
sleep 2; grep -o 'http://127.0.0.1:8799/#n=[^ ]*' /tmp/claude-1000/-home-eugene/f5f9b726-c9a6-477c-8138-ceb693c7b085/scratchpad/ui-demo-serve.log
```
The one-time link is the handle for the script below. Do not print the `api token:` line anywhere else; delete the log when done.

- [ ] **Step 2: Write dashboard/e2e/screenshots.mjs**

```js
// Usage: node e2e/screenshots.mjs "<one-time link>" <outdir>
import { chromium } from "/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.mjs";
import { homedir } from "node:os";

const [link, out] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: `${homedir()}/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell` });
const shots = [];
for (const [name, viewport] of [["phone", { width: 390, height: 664 }], ["desktop", { width: 1280, height: 800 }]]) {
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport, colorScheme: scheme });
    const page = await ctx.newPage();
    if (!shots.length) await page.goto(link); else await page.goto(link.split("#")[0]);
    await page.waitForSelector("text=Plan");
    for (const route of ["plan", "inbox", "activity", "spend"]) {
      await page.goto(link.split("#")[0] + "#/" + route);
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${out}/${name}-${scheme}-${route}.png`, fullPage: true });
    }
    await page.goto(link.split("#")[0] + "#/plan?node=1");
    await page.waitForSelector("text=Overview");
    const btn = await page.$("text=Approve spec");
    const box = btn ? await btn.boundingBox() : null;
    console.log(name, scheme, "approve-spec box:", JSON.stringify(box), "scrollWidth:", await page.evaluate(() => document.documentElement.scrollWidth));
    await page.screenshot({ path: `${out}/${name}-${scheme}-sheet.png` });
    shots.push(name + scheme);
    await ctx.close();
  }
}
await browser.close();
```

Note: the first context uses the nonce link (it signs in and gets the cookie); later contexts are fresh and read-only. To see actions in every shot, run the script once per scheme with a fresh daemon, or accept read-only shots for the later contexts and verify actions on the first. State in the report which shots are signed in.

- [ ] **Step 3: Run it**

```bash
mkdir -p /tmp/claude-1000/-home-eugene/f5f9b726-c9a6-477c-8138-ceb693c7b085/scratchpad/shots && cd /home/eugene/projects/muvue/dashboard && node e2e/screenshots.mjs "<link>" /tmp/claude-1000/-home-eugene/f5f9b726-c9a6-477c-8138-ceb693c7b085/scratchpad/shots
```
Expected: for `phone light`, the Approve spec box has `y < 300` (visible without scrolling in a 664 px viewport) and `scrollWidth` is `390`. Open each PNG with the Read tool and check: Claude ivory or charcoal background, terracotta primary buttons, tab bar at the bottom on phone, sidebar on desktop, no clipped text.

- [ ] **Step 4: Approve through the UI** — in a follow-up script or the same one, on the signed-in context: `await page.click("text=Approve spec")`, wait 500 ms, then `curl -s http://127.0.0.1:8799/nodes/1 | grep -o '"status": *"[a-z_]*"'` shows `"status": "ready"`.

- [ ] **Step 5: Tear down** — kill the daemon by pid from `ss -ltnp | grep 8799` (never `pkill -f`), delete the serve log, `rm -rf /tmp/muvue-ui-demo`.

- [ ] **Step 6: Commit the script**

```bash
cd /home/eugene/projects/muvue && git add dashboard/e2e/screenshots.mjs && git commit -m "Dashboard: screenshot script for phone and desktop, light and dark

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

- [ ] **Step 7: Report** — list: test counts (vitest and pytest), the Approve spec bounding box at phone size, scrollWidth, which screenshots were signed in, and anything that looked off. Attach the phone light Plan and sheet screenshots. Do not push until the user has seen the screenshots.

---

## Self-review notes

- Spec coverage: shell (T7), tokens (T5), Plan with diagram/list/spec card and both gates (T8), task sheet with actions first and Overview/Diff/Logs (T9), spec page (T10), inbox with all nine sections (T11), activity timeline chips and revisions (T12), spend (T13), palette and close sheet (T14), packaging/tests/CI/docs (T1, T15), `GET /agents` (T2), verification (T16). The "no `alert/confirm/prompt`" rule holds: pause and reject confirm in sheets; results are toasts.
- Names used across tasks: `api`, `post`, `routes.*`, `refresh`, `refreshTick`, `projectId`, `currentProject`, `authed`, `inboxCount`, `toast`, `toastError`, `openNode`, `closeNode`, `navigate`, `route`, `resetRouteFromLocation`, `NodeDetail`, `Actions`, `Sheet`, `Button`, `Pill`, `Tier`, `Segmented`, `Empty`, `NodeRowItem`, `classifyDiffLine`, `countInbox`, `paletteItems`, `layout`, `BOX_W`, `BOX_H`. Each is defined in the task that first uses it.
- Known judgement calls for the implementer: if `@testing-library/jest-dom` matchers are unavailable, use `toBeTruthy()` on `screen.getBy*` results instead of `toBeInTheDocument()`. If `color-mix` is unsupported in the headless shell used for screenshots (it is supported in Chromium 111+), fall back to fixed tint hexes in `ui.css` and say so.
