# Dashboard Clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the muvue dashboard say, at every moment, what is happening and what to do next: every launch shows instant, persistent progress and any failure on screen; status words are honest; and the plan renders as a real top-down DAG on phone and desktop.

**Architecture:** The server becomes the source of truth for "what is running": a new `GET /projects/{id}/activity` reports live runner/breakdown processes and each node's latest breakdown outcome, and the dashboard renders an activity bar from it (replacing client-only "ghost" boxes that never saw failures). A pure `nextStep()` function turns project state into one guided next action. The canvas gets one top-down layered layout (spec on top, arrows with arrowheads, fixed box heights so nothing overlaps) used at every width, replacing the separate phone list.

**Tech Stack:** Python 3.12 / FastAPI / SQLite (backend, `uv run pytest`); Preact + @preact/signals + Vite + Vitest + TypeScript (dashboard, `dashboard/`); headless Chromium via `playwright-core` for the UI check script (dev-only, not a dependency).

**Spec:** This plan's own **Diagnosis** section below is the spec: it records the defects observed on 2026-09-28 against a live instance and a scratch repo, with evidence. The earlier design this builds on is `docs/superpowers/specs/2026-09-27-project-canvas-design.md` (read "The canvas" and "Notification cards" sections for vocabulary; where it conflicts with the Diagnosis, the Diagnosis wins because it records what users actually hit).

## Diagnosis (the spec)

User reports, verbatim: "why does it say fake agent? how do i see diagram?", "it all is quite hard to read and understand", "Its just unclear what run is vs breakdown with agent. No DAG anywhere either", "Each time i launch something, the loading should be hyper clear".

Evidence gathered (scratch repo driven in headless Chromium at 390×844 and 1280×800, plus the live `voxscore` project's `events` table):

| # | Defect | Evidence | Fixed in |
|---|---|---|---|
| D1 | "Break down with agent" failed 3 times on voxscore and the UI showed nothing. The built-in `fake` agent's default `cooperative` behavior never answers a breakdown brief, so `_breakdown` records `breakdown.failed {"reason": "agent produced no parseable breakdown"}` ~35 ms after start. | voxscore `events` ids 4–9; `.muvue/logs/breakdown-1.log` shows `behavior=cooperative` result lines | Task 2 (agent), Task 6 (failure shown) |
| D2 | Launch feedback is invisible. 150 ms after tapping "Break down with agent" on phone the only change is a hover tint; the phone view renders no ghost/progress at all (ghosts exist only in the desktop `Canvas`). `▶ Run` shows nothing but a 4 s toast "run started". | screenshots `diag-02`, `diag-05` | Tasks 5, 6 |
| D3 | Failures are never surfaced: `breakdown.failed` is not read by the dashboard; the desktop ghost for a failed breakdown spins forever (it is only cleared when children appear), and `pending.ts`'s `startChildIds: new Set()` makes it clear instantly when tasks already exist. The ghost "view log" link is a relative file path (`.muvue/logs/breakdown-1.log`) that 404s. | `AddForm.tsx:89-91`, `Canvas.tsx:61`, `pending.ts:33-35` | Task 6 |
| D4 | Breakdown logs are unreachable from the dashboard: `core.queries.tail_log` reads only `run-<id>.log` and `<id>.log`, never `breakdown-<id>.log`; the whole-project run log `run-project-<id>.log` has no endpoint. | `src/muvue/core/queries.py:229-239` | Task 3 |
| D5 | `▶ Run` in the planning phase starts a runner that does nothing (`{"processed": [], "cycles": 1}` in `run-project-1.log`) yet reports "run started". Run is never disabled and never says why nothing happens. | voxscore `run-project-1.log` | Tasks 4, 8 |
| D6 | Status words lie: every planned-but-unapproved task says `fake · queued` (it cannot run until the task list is approved); the approved spec says `fake · queued` (a spec is never queued); the status line always says `$0.00 of $1.00` (hard-coded `spend={0} budget={1}` in `ProjectPage.tsx:77`). | screenshot `diag-03`, `ProjectPage.tsx:77` | Task 7 |
| D7 | No explanation that `fake` is a demo agent that writes no code. | user question; `.muvue/config.toml` `[routing]` all `fake` | Task 7 |
| D8 | No guidance on what to do next; the same approval appears twice ("Approve" in the card rail and "Approve task list" on the spec box). | screenshot `diag-03`, `diag-04` | Task 8 |
| D9 | No DAG on phone: `PhoneFlow` renders a plain vertical list with no arrows and hides `carries` labels. On desktop the DAG is broken: the spec card overlaps the first row of task boxes (the spec slot reserves 96 px, the card is taller), arrows are `var(--border)`-colored with no arrowheads, there is no spec→task edge, arrow labels are clipped to a fixed 80 px ("audio f…"), the third column is cut off with no fit-to-view, and the "fit" button uses a clock icon. | screenshots `diag-03`, `diag-07`; `Canvas.tsx:48-58` | Task 9 |
| D10 | The phone/desktop switch is `window.innerWidth < 900` read once per render (`ProjectPage.tsx:19`), so rotating or resizing keeps the wrong layout. | screenshot `diag-04` (1280 px wide page still showing the phone list) | Task 9 |
| D11 | Task boxes carry an emoji-only `✨` button and a `+ Subtask` button that add clutter and uncertain height; the node sheet's "Flow" section is always passed empty arrays (`NodeSheet.tsx:49`), so "receives from / sends to" never shows; the Runs list shows `breakdown.failed` without its reason. | `TaskBox.tsx:40-55`, `NodeSheet.tsx:49`, `Runs.tsx:28-29` | Tasks 9, 10 |
| D12 | Desktop sidebar: the project button has `flex: 1` inside a column flexbox, so it stretches to fill the sidebar and pushes the project list to the bottom. | screenshot `diag-07`; `shell.css:4` | Task 9 |

Diagnostic screenshots live in the session scratchpad (`diag-01`…`diag-07`); Task 1's script regenerates equivalent evidence on demand.

## Global Constraints

- No new runtime dependencies: nothing added to `pyproject.toml` `dependencies` or `dashboard/package.json` `dependencies`. The UI check script loads `playwright-core` from a path given in an environment variable; it is not added to `devDependencies` either.
- Every dashboard task ends with, from `dashboard/`: `npm run typecheck` (0 errors), `npm test` (all pass), `npm run build` (copies the bundle to `src/muvue/api/static/index.html`); commit the rebuilt `src/muvue/api/static/index.html` in the same commit. `tsc` was skipped in the last plan and 18 type errors shipped — do not skip it.
- Every backend task ends with `uv run pytest -q` fully green.
- Every tappable control keeps a ≥44 px touch target (`dashboard/test/touch-targets.test.ts` pattern).
- Fail loudly: every failed launch (breakdown, run, start) is shown on the page as text that stays until the user dismisses it — never only as a toast, never swallowed. No silent `catch {}`.
- Browser storage only for per-viewer dismissals, wrapped in `try/catch`; the page must work when storage throws.
- User-facing copy: use the exact strings written in this plan's code blocks.
- No inline HTML event-handler attributes and no external resources in the built page (`tests/test_dashboard_static.py`).
- Every API path the dashboard calls is spelled only in `dashboard/src/api/routes.ts` (`tests/test_dashboard_static.py::test_every_route_in_routes_ts_is_served` checks each exists on the daemon).
- Work happens in the worktree `.worktrees/dashboard-clarity` on branch `feat/dashboard-clarity`, created from `main`.
- Commit messages end with the attribution lines the session provides.

---

## File map

Backend:
- `src/muvue/fake_agent.py` — cooperative behavior answers breakdown briefs (Task 2).
- `src/muvue/core/runners.py` — registry entries gain `kind` and `node_id` (Task 3).
- `src/muvue/cli/main.py` — `_breakdown` registers as `kind="breakdown"` (Task 3).
- `src/muvue/core/queries.py` — `tail_log` reads breakdown logs; new `tail_project_log`, `project_activity` (Task 3); new `run_block_reason` (Task 4).
- `src/muvue/api/app.py` — new `GET /projects/{id}/activity`, `GET /projects/{id}/logs` (Task 3); `/projects/{id}/run` preflight 409 (Task 4).

Dashboard (`dashboard/`):
- `scripts/ui-check.mjs` — seeded end-to-end UI check with screenshots (Task 1).
- `src/ui/Button.tsx`, `src/ui/ui.css`, new `src/ui/useAction.ts` — busy state (Task 5).
- new `src/project/activity.ts`, new `src/project/ActivityBar.tsx`, new `src/project/LogSheet.tsx` — server-driven activity (Task 6); delete `src/canvas/pending.ts`, `src/canvas/GhostBox.tsx`.
- `src/canvas/agentState.ts`, `src/canvas/AgentChip.tsx`, `src/project/StatusLine.tsx` — honest wording (Task 7).
- new `src/project/nextStep.ts`, new `src/project/NextStepBar.tsx`; `src/cards/cardsFromInbox.ts` — guided next step, Run gating (Task 8).
- `src/canvas/flowLayout.ts` (rewrite), `src/canvas/Canvas.tsx`, `src/canvas/TaskBox.tsx`, `src/canvas/SpecRoot.tsx`, new `src/canvas/DagPlaceholder.tsx`, `src/canvas/canvas.css`, `src/project/ProjectPage.tsx`, `src/shell/shell.css`; delete `src/canvas/PhoneFlow.tsx` (Task 9).
- `src/node/Actions.tsx`, `src/node/NodeSheet.tsx`, new `src/node/flowLinks.ts`, `src/node/Runs.tsx` (Task 10).
- `CHANGELOG.md`, `docs/decisions.md` (Task 11).

---

### Task 1: UI check script (baseline evidence)

A repeatable, seeded, real-browser check. It is expected to FAIL on the current code — record the baseline failures in the ledger; Task 11 requires it to pass.

**Files:**
- Create: `dashboard/scripts/ui-check.mjs`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the DOM contract later tasks must satisfy (the check names each selector):
  - `svg.dag` — the canvas arrow layer; `svg.dag path.flow-arrow` — one per edge; `svg.dag .arrow-label` and its sibling `rect.arrow-label-bg`
  - `.dag-empty` — the "no tasks yet" placeholder node
  - `[data-activity]` — the activity bar (Task 6)
  - `[data-run-reason]` — visible text explaining why Run is disabled (Task 8)
  - `.task-box`, `.spec-root-card` — DAG boxes
  - button text `✨ Plan tasks with agent` and `▶ Run tasks`

- [ ] **Step 1: Write the script**

```js
// dashboard/scripts/ui-check.mjs
//
// End-to-end UI check for the project canvas. Seeds a throwaway repo,
// starts `muvue serve` on it, drives the dashboard in headless Chromium at
// phone (390x844) and desktop (1280x800) widths, prints PASS/FAIL per
// check, saves screenshots, and exits 1 if any check fails.
//
// Usage (from the muvue repo root):
//   PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core \
//   CHROME_PATH=/path/to/chrome-headless-shell \
//   node dashboard/scripts/ui-check.mjs [out-dir]
//
// playwright-core is loaded from PLAYWRIGHT_CORE on purpose: it is not a
// dependency of the dashboard.
import { createRequire } from "node:module";
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { PLAYWRIGHT_CORE, CHROME_PATH } = process.env;
if (!PLAYWRIGHT_CORE || !CHROME_PATH) {
  console.error("set PLAYWRIGHT_CORE and CHROME_PATH (see header comment)");
  process.exit(2);
}
const { chromium } = require(PLAYWRIGHT_CORE);
const muvueRoot = resolve(new URL("../..", import.meta.url).pathname);
const outDir = resolve(process.argv[2] ?? join(tmpdir(), "muvue-ui-check"));
mkdirSync(outDir, { recursive: true });
const port = Number(process.env.UI_CHECK_PORT ?? 8899);

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -- " + detail : ""}`);
}
function muvue(...args) {
  return execFileSync("uv", ["run", "muvue", ...args], { cwd: muvueRoot, encoding: "utf8" });
}

// --- seed: project + approved spec, no tasks (the voxscore starting state)
const repo = mkdtempSync(join(tmpdir(), "muvue-ui-check-repo-"));
execFileSync("git", ["init", "-q"], { cwd: repo });
execFileSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], { cwd: repo });
muvue("init", repo);
const project = JSON.parse(muvue("project", "create", "--goal", "voxscore demo: record voices, transcribe to sheet music", "--path", repo));
const projectId = project.id ?? project.project.id;
const spec = JSON.parse(muvue("spec", String(projectId), "--title", "voxscore v0.1", "--body", "Record voices from mic\nTrack pitch per voice\nExport MusicXML", "--path", repo));
muvue("approve", `spec:${spec.id}`, "--path", repo);

// --- serve (fake agent in its default behavior: breakdown must still work)
const serve = spawn("uv", ["run", "muvue", "serve", repo, "--port", String(port)], { cwd: muvueRoot, detached: true });
const link = await new Promise((res, rej) => {
  let buf = "";
  const t = setTimeout(() => rej(new Error("serve printed no link in 30s:\n" + buf)), 30000);
  const on = (d) => { buf += d; const m = buf.match(/http:\/\/\S+#n=\S+/); if (m) { clearTimeout(t); res(m[0]); } };
  serve.stdout.on("data", on); serve.stderr.on("data", on);
});

const browser = await chromium.launch({ executablePath: CHROME_PATH });
try {
  const ctx = await browser.newContext({ deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(link);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: join(outDir, "01-phone-no-tasks.png"), fullPage: true });

  check("phone: empty-state placeholder node is shown", await page.locator(".dag-empty").count() === 1);
  const runReason = page.locator("[data-run-reason]");
  check("phone: Run explains why it is disabled", (await runReason.count()) > 0 && /approve/i.test(await runReason.first().innerText()));
  check("phone: no horizontal page scroll", await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth));

  await page.getByText("✨ Plan tasks with agent").first().click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(outDir, "02-phone-300ms-after-plan.png"), fullPage: true });
  check("phone: activity bar visible within 300ms of launching", await page.locator("[data-activity]").isVisible());

  await page.waitForFunction(() => document.querySelectorAll(".task-box").length >= 3, null, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(800);
  await page.screenshot({ path: join(outDir, "03-phone-after-plan.png"), fullPage: true });
  await dagChecks(page, "phone");

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: join(outDir, "04-desktop-after-resize.png") });
  await dagChecks(page, "desktop (resized, no reload)");
  check("desktop: no horizontal page scroll", await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth));
} finally {
  await browser.close();
  try { process.kill(-serve.pid, "SIGTERM"); } catch {}
}

async function dagChecks(page, label) {
  const boxes = await page.locator(".task-box, .spec-root-card").evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; }));
  check(`${label}: spec + 3 task boxes rendered`, boxes.length === 4, `got ${boxes.length}`);
  let overlap = false;
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) overlap = true;
  }
  check(`${label}: no two boxes overlap`, !overlap);
  const arrows = await page.locator("svg.dag path.flow-arrow").count();
  check(`${label}: 3 arrows (spec->first task + 2 dependencies)`, arrows === 3, `got ${arrows}`);
  const clipped = await page.locator("svg.dag .arrow-label").evaluateAll((els) => els.filter((t) => {
    const bg = t.parentNode.querySelector("rect.arrow-label-bg");
    return !bg || t.getBBox().width > bg.getBBox().width;
  }).length);
  check(`${label}: arrow labels fit their backgrounds`, clipped === 0, `${clipped} clipped`);
  const offscreen = await page.locator(".task-box").evaluateAll((els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.left < 0 || r.right > window.innerWidth; }).length);
  check(`${label}: every task box is within the viewport width`, offscreen === 0, `${offscreen} cut off`);
}

console.log(`\nscreenshots: ${outDir}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Run it against the current code and record the baseline**

Run (from the worktree root; on this machine the paths are `PLAYWRIGHT_CORE=/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core` and `CHROME_PATH=/home/eugene/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell`):

`node dashboard/scripts/ui-check.mjs /tmp/ui-check-baseline`

Expected: exits 1. Expected FAILs include the empty-state placeholder, Run reason, activity bar, arrow count, and box overlap checks. Paste the PASS/FAIL list into the task report.

- [ ] **Step 3: Commit**

```bash
git add dashboard/scripts/ui-check.mjs
git commit -m "Add a seeded headless-browser UI check for the project canvas"
```

---

### Task 2: The fake agent answers breakdown briefs

Fixes D1. The `cooperative` default must cooperate with a breakdown brief, so a fresh `muvue init` project (which routes everything to `fake`) can be planned from the dashboard.

**Files:**
- Modify: `src/muvue/fake_agent.py` (`_run`, near the top of the function)
- Test: `tests/test_fake_agent_processes.py` (append)

**Interfaces:**
- Consumes: `core.cli.main._breakdown`'s brief text, which always contains the literal `{"type": "breakdown", "children": ` (`src/muvue/cli/main.py:962`).
- Produces: nothing new for other tasks.

- [ ] **Step 1: Write the failing test** (append to `tests/test_fake_agent_processes.py`)

```python
def test_cooperative_fake_agent_answers_a_breakdown_brief(capsys):
    """The default behavior must answer a breakdown brief with a breakdown
    line: a fresh project routes every kind to `fake`, and a breakdown
    that always fails with "agent produced no parseable breakdown" made
    the dashboard's planning button look broken (voxscore, 2026-09-28)."""
    from muvue import fake_agent
    from muvue.core.drivers import parse_breakdown

    brief = 'Break down this spec...\nReply with exactly one line: {"type": "breakdown", "children": [...]}'
    assert fake_agent._run("cooperative", brief) == 0
    children = parse_breakdown(capsys.readouterr().out)
    assert [c["title"] for c in children] == ["Record voice", "Detect pitch", "Export MusicXML"]


def test_cooperative_fake_agent_still_does_normal_work_for_a_task_brief(capsys):
    from muvue import fake_agent

    assert fake_agent._run("cooperative", "implement the task") == 0
    assert '"status": "done"' in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `uv run pytest tests/test_fake_agent_processes.py -k breakdown_brief -v`
Expected: FAIL — `parse_breakdown` returns `[]`, so the titles list is empty.

- [ ] **Step 3: Implement** — in `_run`, directly after the two `progress` prints and before `if behavior == "slow":`, add:

```python
    # `muvue _breakdown`'s brief asks for a breakdown line. A cooperative
    # agent answers what it was asked, so the default behavior plans
    # instead of reporting generic "did the work" (which `_breakdown`
    # can only record as a failure).
    if behavior == "cooperative" and '{"type": "breakdown"' in brief_raw:
        behavior = "breakdown"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fake_agent_processes.py tests/test_cli_breakdown.py tests/test_fake_agent.py -v`
Expected: PASS. Then `uv run pytest -q`: all green.

- [ ] **Step 5: Commit**

```bash
git add src/muvue/fake_agent.py tests/test_fake_agent_processes.py
git commit -m "Fake agent: cooperative behavior answers breakdown briefs"
```

---

### Task 3: Server-side activity and reachable logs

Fixes D4 and gives the dashboard a server source of truth for "what is running / what failed" (needed by Task 6).

**Files:**
- Modify: `src/muvue/core/runners.py` (`register`)
- Modify: `src/muvue/cli/main.py` (`_breakdown`'s `core_runners.register(...)` call, line ~953)
- Modify: `src/muvue/core/queries.py` (`tail_log`; add `tail_project_log`, `project_activity`)
- Modify: `src/muvue/api/app.py` (add two GET routes next to `/projects/{project_id}/revisions`)
- Test: `tests/test_queries.py` (append), `tests/test_api.py` (append)

**Interfaces:**
- Produces:
  - `core.runners.register(repo_root: Path, project_id: int | None, kind: str = "run", node_id: int | None = None) -> Path` — entry JSON gains `"kind"` and `"node_id"`.
  - `core.queries.tail_project_log(repo_root, project_id: int, lines: int) -> str`
  - `core.queries.project_activity(conn, repo_root, project_id: int) -> dict` with shape:
    ```json
    {"active": [{"kind": "breakdown", "pid": 123, "node_id": 5, "started_at": "2026-09-28T13:23:35Z"}],
     "breakdowns": [{"node_id": 5, "event_id": 9, "type": "breakdown.failed", "ts": "…", "agent": "fake", "reason": "…", "created": null}],
     "working": [{"node_id": 7, "title": "Detect pitch", "agent": "claude"}]}
    ```
    `breakdowns` holds the latest `breakdown.*` event per node in the project (`agent` comes from that node's latest `breakdown.started`; `reason`/`created` from the payload, else `null`). `working` holds the project's `in_progress` nodes (`agent` = owner with any `runner:` prefix stripped). `active` holds live registry entries whose `project_id` equals `project_id` or is `null`; entries written before this change have no `kind` and are reported as `"run"`.
  - `GET /projects/{project_id}/activity` → the dict above (session required, 404 for an unknown project).
  - `GET /projects/{project_id}/logs?lines=200` → plain text tail of `.muvue/logs/run-project-<id>.log` (session required).
  - `GET /nodes/{id}/logs` now also includes `breakdown-<id>.log`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queries.py` (reuse the file's existing fixtures for a connection and repo; if it has none that give both, create them inline as below):

```python
def test_tail_log_includes_breakdown_log(tmp_path):
    from muvue.core import queries

    logs = tmp_path / ".muvue" / "logs"
    logs.mkdir(parents=True)
    (logs / "breakdown-4.log").write_text("planning line\n")
    assert "planning line" in queries.tail_log(tmp_path, 4, 50)


def test_tail_project_log_reads_run_project_log(tmp_path):
    from muvue.core import queries

    logs = tmp_path / ".muvue" / "logs"
    logs.mkdir(parents=True)
    (logs / "run-project-2.log").write_text("a\nb\nc\n")
    assert queries.tail_project_log(tmp_path, 2, 2) == "b\nc\n"
    assert queries.tail_project_log(tmp_path, 3, 2) == ""


def test_project_activity_reports_latest_breakdown_outcome_and_live_runners(tmp_path, monkeypatch):
    from muvue.core import db as core_db, events, nodes, projects, queries, runners
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        p = projects.create_project(conn, goal="g")
        spec = nodes.create_node(conn, project_id=p["id"], kind="spec", title="s", criteria=[], criteria_mode="manual", predicted_touches=[], status="ready")
        for type_, payload in [("breakdown.started", {"agent": "fake"}), ("breakdown.failed", {"reason": "boom"})]:
            events.record_event(conn, project_id=p["id"], node_id=spec["id"], actor="agent", actor_evidence="tty", type_=type_, payload=payload)
        monkeypatch.setattr(runners, "live", lambda root: [
            {"pid": 11, "project_id": p["id"], "started_at": "t", "kind": "breakdown", "node_id": spec["id"]},
            {"pid": 12, "project_id": p["id"] + 1, "started_at": "t"},
            {"pid": 13, "project_id": None, "started_at": "t"},
        ])
        a = queries.project_activity(conn, tmp_path, p["id"])
    finally:
        conn.close()
    assert a["active"] == [
        {"kind": "breakdown", "pid": 11, "node_id": spec["id"], "started_at": "t"},
        {"kind": "run", "pid": 13, "node_id": None, "started_at": "t"},
    ]
    [b] = a["breakdowns"]
    assert (b["node_id"], b["type"], b["agent"], b["reason"], b["created"]) == (spec["id"], "breakdown.failed", "fake", "boom", None)
    assert a["working"] == []


def test_register_records_kind_and_node(tmp_path):
    import json
    from muvue.core import runners

    path = runners.register(tmp_path, 3, kind="breakdown", node_id=9)
    entry = json.loads(path.read_text())
    assert (entry["kind"], entry["node_id"], entry["project_id"]) == ("breakdown", 9, 3)
```

If `nodes.create_node`'s keyword set differs from the call above, copy the exact call shape used by `tests/test_api.py::ready_task` (it is the reference for creating a node in tests).

Append to `tests/test_api.py`:

```python
def test_project_activity_endpoint(client, conn, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.get(f"/projects/{project_id}/activity", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"active", "breakdowns", "working"}
    assert client.get("/projects/9999/activity", headers=auth_headers).status_code == 404
    assert client.get(f"/projects/{project_id}/activity").status_code == 403


def test_project_logs_endpoint(client, repo, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    (repo / ".muvue" / "logs").mkdir(parents=True, exist_ok=True)
    (repo / ".muvue" / "logs" / f"run-project-{project_id}.log").write_text("runner said hi\n")
    r = client.get(f"/projects/{project_id}/logs", headers=auth_headers)
    assert r.status_code == 200
    assert "runner said hi" in r.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_queries.py tests/test_api.py -k "breakdown_log or project_log or project_activity or register_records" -v`
Expected: FAIL (`AttributeError: module 'muvue.core.queries' has no attribute 'tail_project_log'`, 404s for the new routes, `TypeError` on `register(..., kind=...)`).

- [ ] **Step 3: Implement `register`**

In `src/muvue/core/runners.py`, replace `register` and update the module docstring's entry shape to `{"pid", "project_id", "started_at", "kind", "node_id"}`:

```python
def register(repo_root: Path, project_id: int | None, kind: str = "run", node_id: int | None = None) -> Path:
    path = _dir(repo_root) / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": os.getpid(),
        "project_id": project_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "node_id": node_id,
    }))
    return path
```

In `src/muvue/cli/main.py` `_breakdown`, change `core_runners.register(repo_root, node["project_id"])` to:

```python
        core_runners.register(repo_root, node["project_id"], kind="breakdown", node_id=node_id)
```

- [ ] **Step 4: Implement the queries** in `src/muvue/core/queries.py`

Replace `tail_log` and add the two new functions right after it. Add `import json` and `from . import runners as runners_mod` to the module's imports (matching its existing `from . import db as db_mod` style). Call `runners_mod.live(...)` through the module attribute, never `from .runners import live` — the test monkeypatches `runners.live`:

```python
def _tail_files(paths: list[Path], lines: int) -> str:
    tail: deque[str] = deque(maxlen=max(lines, 0))
    for path in paths:
        if path.exists():
            with open(path, errors="replace") as f:
                tail.extend(f)
    return "".join(tail)


def tail_log(repo_root: str | Path, node_id: int, lines: int) -> str:
    """The last `lines` lines of a node's agent output: its breakdown
    log, then `run-<id>.log` from a dashboard-started run, then the
    runner's `<id>.log`."""
    logs = Path(repo_root) / ".muvue" / "logs"
    return _tail_files([logs / f"breakdown-{node_id}.log", logs / f"run-{node_id}.log", logs / f"{node_id}.log"], lines)


def tail_project_log(repo_root: str | Path, project_id: int, lines: int) -> str:
    """The last `lines` lines of a dashboard-started whole-project run
    (`POST /projects/{id}/run` writes `run-project-<id>.log`)."""
    return _tail_files([Path(repo_root) / ".muvue" / "logs" / f"run-project-{project_id}.log"], lines)


def project_activity(conn: sqlite3.Connection, repo_root: str | Path, project_id: int) -> dict:
    """What is running for a project right now and how each node's
    latest breakdown ended -- the dashboard's activity bar reads this
    instead of guessing from its own clicks."""
    active = [
        {"kind": r.get("kind", "run"), "pid": r["pid"], "node_id": r.get("node_id"), "started_at": r["started_at"]}
        for r in runners_mod.live(Path(repo_root))
        if r.get("project_id") in (None, project_id)
    ]
    rows = db_mod.query_all(
        conn,
        "SELECT id, node_id, type, ts, payload FROM events WHERE project_id = ? "
        "AND type IN ('breakdown.started', 'breakdown.finished', 'breakdown.failed') ORDER BY id",
        (project_id,),
    )
    latest: dict[int, dict] = {}
    agent_of: dict[int, str | None] = {}
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        if row["type"] == "breakdown.started":
            agent_of[row["node_id"]] = payload.get("agent")
        latest[row["node_id"]] = {
            "node_id": row["node_id"], "event_id": row["id"], "type": row["type"], "ts": row["ts"],
            "agent": agent_of.get(row["node_id"]), "reason": payload.get("reason"), "created": payload.get("created"),
        }
    working = [
        {"node_id": r["id"], "title": r["title"],
         "agent": (r["owner"] or "").split(":", 1)[1] if (r["owner"] or "").startswith("runner:") else r["owner"]}
        for r in db_mod.query_all(
            conn,
            "SELECT id, title, owner FROM nodes WHERE project_id = ? AND status = 'in_progress' AND deleted_at IS NULL ORDER BY id",
            (project_id,),
        )
    ]
    return {"active": active, "breakdowns": list(latest.values()), "working": working}
```

`db_mod`, `deque`, `Path`, `sqlite3` are already used in this module (`tail_log`, `touch_drift`); verify the names at the top of the file and match them.

- [ ] **Step 5: Add the routes** in `src/muvue/api/app.py`, directly after the `/projects/{project_id}/revisions` handler:

```python
    @app.get("/projects/{project_id}/activity")
    def project_activity(request: Request, project_id: int) -> dict:
        """Live runner/breakdown processes and each node's latest
        breakdown outcome (`core.queries.project_activity`), for the
        dashboard's activity bar."""
        _require_session(request)
        with _conn() as conn:
            try:
                core.projects.get_project(conn, project_id)
                return core.queries.project_activity(conn, repo_root, project_id)
            except Exception as e:
                _handle_core_error(e)

    @app.get("/projects/{project_id}/logs", response_class=PlainTextResponse)
    def project_logs(request: Request, project_id: int, lines: int = 200) -> str:
        """Tail of the whole-project run log started by `POST /projects/{id}/run`."""
        _require_session(request)
        with _conn() as conn:
            try:
                core.projects.get_project(conn, project_id)
            except Exception as e:
                _handle_core_error(e)
        return core.queries.tail_project_log(repo_root, project_id, min(max(lines, 1), 5000))
```

Check that `_handle_core_error` maps the `LookupError` from `get_project` to 404 (it does for `/projects/{project_id}` at `app.py:543`; mirror that handler if it catches a narrower exception).

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_queries.py tests/test_api.py tests/test_cli_breakdown.py tests/test_pause_control.py -v` → PASS. Then `uv run pytest -q` → all green.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/runners.py src/muvue/cli/main.py src/muvue/core/queries.py src/muvue/api/app.py tests/test_queries.py tests/test_api.py
git commit -m "API: project activity endpoint, project run log, breakdown logs in node logs"
```

---

### Task 4: `Run` refuses loudly when nothing can run

Fixes the backend half of D5: `POST /projects/{id}/run` returns 409 with a reason instead of spawning a runner that does nothing.

**Files:**
- Modify: `src/muvue/core/queries.py` (add `run_block_reason`)
- Modify: `src/muvue/api/app.py` (`start_run`, line ~843)
- Test: `tests/test_queries.py` (append), `tests/test_api.py` (modify `test_run_endpoint_spawns_process`, append)

**Interfaces:**
- Produces: `core.queries.run_block_reason(conn, project_id: int) -> str | None`. Exact strings (the dashboard shows them verbatim):
  - phase `planning`: `"Nothing to run yet: approve the task list first."`
  - phase `paused`: `"The project is paused. Resume it first."`
  - phase `closed`: `"The project is closed."`
  - phase `executing` with no `task`/`subtask` in status `ready` (not deleted): `"Nothing is ready to run: every task is done, running, waiting on review, or waiting on earlier tasks."`
  - otherwise `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_queries.py`:

```python
def test_run_block_reason_by_phase_and_readiness(tmp_path):
    from muvue.core import db as core_db, gates, nodes, projects, queries
    from muvue.core.config import MuvueConfig
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    conn = core_db.connect(tmp_path / ".muvue" / "muvue.db")
    try:
        p = projects.create_project(conn, goal="g")
        assert queries.run_block_reason(conn, p["id"]) == "Nothing to run yet: approve the task list first."
        task = nodes.create_node(conn, project_id=p["id"], kind="task", title="t", criteria=["passes"], criteria_mode="auto", predicted_touches=["a.py"], status="pending")
        gates.approve_gate2(conn, p["id"], config=MuvueConfig())
        assert nodes.get_node(conn, task["id"])["status"] == "ready"
        assert queries.run_block_reason(conn, p["id"]) is None
        conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (task["id"],))
        conn.commit()
        assert queries.run_block_reason(conn, p["id"]).startswith("Nothing is ready to run")
        conn.execute("UPDATE projects SET phase = 'paused' WHERE id = ?", (p["id"],))
        conn.commit()
        assert queries.run_block_reason(conn, p["id"]) == "The project is paused. Resume it first."
    finally:
        conn.close()
```

In `tests/test_api.py`, replace `test_run_endpoint_spawns_process` with the two tests below (the old test ran a project in the planning phase, which is exactly the no-op this task forbids):

```python
def test_run_endpoint_spawns_process_when_a_task_is_ready(client, ready_task, auth_headers):
    r = client.post(f"/projects/{ready_task['project']['id']}/run", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "pid" in r.json()["spawned"]


def test_run_endpoint_409s_with_a_reason_in_planning(client, auth_headers):
    r = client.post("/projects", json={"goal": "g"}, headers=auth_headers)
    project_id = r.json()["project"]["id"]
    r = client.post(f"/projects/{project_id}/run", json={}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "Nothing to run yet: approve the task list first."
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_queries.py tests/test_api.py -k "run_block_reason or run_endpoint" -v`
Expected: FAIL (`AttributeError: ... run_block_reason`; the planning-phase POST returns 200).

- [ ] **Step 3: Implement** — add to `src/muvue/core/queries.py`:

```python
def run_block_reason(conn: sqlite3.Connection, project_id: int) -> str | None:
    """Why `run` would do nothing for this project, or None if some task
    is ready. `POST /projects/{id}/run` refuses with this text instead of
    spawning a runner that exits having processed nothing."""
    phase = db_mod.query_one(conn, "SELECT phase FROM projects WHERE id = ?", (project_id,))["phase"]
    if phase == "planning":
        return "Nothing to run yet: approve the task list first."
    if phase == "paused":
        return "The project is paused. Resume it first."
    if phase == "closed":
        return "The project is closed."
    ready = db_mod.query_one(
        conn,
        "SELECT COUNT(*) AS n FROM nodes WHERE project_id = ? AND status = 'ready' "
        "AND kind IN ('task', 'subtask') AND deleted_at IS NULL",
        (project_id,),
    )["n"]
    if not ready:
        return "Nothing is ready to run: every task is done, running, waiting on review, or waiting on earlier tasks."
    return None
```

If `db_mod` has no `query_one`, use `query_all(...)[0]` (check `src/muvue/core/db.py` for the helper names).

In `src/muvue/api/app.py` `start_run`, replace the `with _conn()` block with:

```python
        with _conn() as conn:
            try:
                core.projects.get_project(conn, project_id)
            except Exception as e:
                _handle_core_error(e)
            reason = core.queries.run_block_reason(conn, project_id)
        if reason:
            raise HTTPException(status_code=409, detail=reason)
```

and update the docstring's first sentence to say it refuses with 409 when `core.queries.run_block_reason` gives a reason.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_queries.py tests/test_api.py -v` → PASS; `uv run pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add src/muvue/core/queries.py src/muvue/api/app.py tests/test_queries.py tests/test_api.py
git commit -m "API: run refuses with a reason when nothing can run"
```

---

### Task 5: Every action button shows it is working

Fixes the per-button half of D2: a pressed button immediately shows a spinner and a "…ing" label, is disabled while in flight (no double launches), and shows its failure inline.

**Files:**
- Create: `dashboard/src/ui/useAction.ts`
- Modify: `dashboard/src/ui/Button.tsx`, `dashboard/src/ui/ui.css`
- Modify (adopt the hook): `dashboard/src/canvas/AddForm.tsx`, `dashboard/src/canvas/SpecRoot.tsx` (`SubmitSpecForm`, `approveSpec`, `approveTasks`), `dashboard/src/node/Actions.tsx`, `dashboard/src/node/StartPicker.tsx`, `dashboard/src/node/Discussion.tsx`, `dashboard/src/node/SpecBody.tsx`, `dashboard/src/cards/Card.tsx`, `dashboard/src/cards/Discuss.tsx`, `dashboard/src/shell/ProjectMenu.tsx`, `dashboard/src/project/CloseSheet.tsx`, `dashboard/src/project/ProjectPage.tsx` (`run`)
- Test: `dashboard/test/useAction.test.tsx` (create), `dashboard/test/ui.test.tsx` (append), `dashboard/test/AddForm.test.tsx` (update)

**Interfaces:**
- Produces:
  - `useAction(): { busy: boolean; error: string | null; run: (fn: () => Promise<unknown>) => Promise<boolean>; clearError: () => void }` — `run` returns `false` without calling `fn` while busy; sets `error` to the message on rejection (and still calls `toastError`), returns `true` on success.
  - `Button` gains `busy?: boolean` and `busyLabel?: string`. When `busy`: renders `<span class="spinner" aria-hidden="true" />` then `busyLabel ?? children`, sets `aria-busy="true"` and `disabled`.
- AddForm no longer uses `pending.ts` create ghosts (`startCreate`/`resolveCreate`/`failCreate`); its Save button shows `Saving…` and its error renders inline as `<div class="callout danger">`. Canvas's `pendingCreateGhosts` rendering is removed in this task (Task 6 deletes `pending.ts` itself).

- [ ] **Step 1: Write the failing tests**

`dashboard/test/useAction.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { useAction } from "../src/ui/useAction";
import { Button } from "../src/ui/Button";

function Harness({ fn }: { fn: () => Promise<unknown> }) {
  const a = useAction();
  return (
    <div>
      <Button busy={a.busy} busyLabel="Starting…" onClick={() => void a.run(fn)}>Go</Button>
      {a.error ? <div role="alert">{a.error}</div> : null}
    </div>
  );
}

test("shows the busy label and blocks a second launch while in flight", async () => {
  let release!: () => void;
  const fn = vi.fn(() => new Promise<void>((r) => { release = r; }));
  render(<Harness fn={fn} />);
  fireEvent.click(screen.getByText("Go"));
  await waitFor(() => screen.getByText("Starting…"));
  expect(screen.getByRole("button")).toBeDisabled();
  expect(screen.getByRole("button")).toHaveAttribute("aria-busy", "true");
  fireEvent.click(screen.getByRole("button"));
  expect(fn).toHaveBeenCalledTimes(1);
  release();
  await waitFor(() => screen.getByText("Go"));
});

test("a failed action leaves its message on screen", async () => {
  render(<Harness fn={() => Promise.reject(new Error("unknown agent 'x'"))} />);
  fireEvent.click(screen.getByText("Go"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("unknown agent 'x'"));
});
```

Append to `dashboard/test/ui.test.tsx`:

```tsx
test("Button busy renders a spinner and disables", () => {
  const { container } = render(<Button busy>Save</Button>);
  expect(container.querySelector(".spinner")).toBeTruthy();
  expect(container.querySelector("button")).toBeDisabled();
});
```

(Add the `Button` import to that file's imports if it is not there.)

In `dashboard/test/AddForm.test.tsx`, replace any assertion about `pendingCreates` / ghost entries with:

```tsx
test("Save shows Saving… while the create request is in flight", async () => {
  let release!: (v: unknown) => void;
  vi.spyOn(client, "post").mockImplementation(() => new Promise((r) => { release = r; }));
  render(<AddForm parentId={1} kind="task" candidates={[]} onClose={() => {}} />);
  fireEvent.input(screen.getByPlaceholderText("title"), { target: { value: "Record voice" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => screen.getByText("Saving…"));
  release({ node: { id: 2 } });
});
```

(import `* as client from "../src/api/client"` and `fireEvent, waitFor` if missing.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/useAction.test.tsx test/ui.test.tsx test/AddForm.test.tsx`
Expected: FAIL (`Cannot find module '../src/ui/useAction'`, no `.spinner`, no `Saving…`).

- [ ] **Step 3: Implement the hook and Button**

`dashboard/src/ui/useAction.ts`:

```ts
import { useRef, useState } from "preact/hooks";
import { toastError } from "../state";

// One in-flight guard + visible error per action button. `run` refuses to
// start a second call while one is pending, so a double tap cannot launch
// two breakdowns or two runs.
export function useAction(): { busy: boolean; error: string | null; run: (fn: () => Promise<unknown>) => Promise<boolean>; clearError: () => void } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      toastError(e);
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return { busy, error, run, clearError: () => setError(null) };
}
```

`dashboard/src/ui/Button.tsx`:

```tsx
import type { ComponentChildren } from "preact";

type Props = {
  variant?: "filled" | "outline" | "danger" | "plain";
  onClick?: (e: MouseEvent) => void;
  disabled?: boolean;
  busy?: boolean;
  busyLabel?: string;
  type?: "button" | "submit";
  children: ComponentChildren;
};

export function Button({ variant = "outline", onClick, disabled, busy, busyLabel, type = "button", children }: Props) {
  return (
    <button type={type} class={"btn btn-" + variant + (busy ? " busy" : "")} onClick={onClick} disabled={disabled || busy} aria-busy={busy ? "true" : undefined}>
      {busy ? <span class="spinner" aria-hidden="true" /> : null}
      {busy ? busyLabel ?? children : children}
    </button>
  );
}
```

Append to `dashboard/src/ui/ui.css`:

```css
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
.btn.busy:disabled { opacity: 1; cursor: progress; }
.spinner { width: 14px; height: 14px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: spin .7s linear infinite; flex: none; }
@keyframes spin { to { transform: rotate(360deg); } }
```

(`base.css`'s `prefers-reduced-motion` rule stops the spin; the "…ing" label still shows, which is the point.)

- [ ] **Step 4: Adopt `useAction` at every launch site**

Pattern — each async handler becomes `a.run(() => post(...)).then((ok) => { if (ok) { toast(...); refresh(); ... } })`, and its button gets `busy={a.busy} busyLabel="…"`. Use one `useAction()` per independent button. Busy labels (exact):

| File | Button | busyLabel |
|---|---|---|
| `canvas/AddForm.tsx` | Save | `Saving…` |
| `canvas/AddForm.tsx` `BreakdownButton` | Break down | `Starting…` |
| `canvas/SpecRoot.tsx` `SubmitSpecForm` | Submit spec | `Submitting…` |
| `node/Actions.tsx` | Approve / Approve changed criteria / Approve spec | `Approving…` |
| `node/Actions.tsx` | Send (reject) | `Sending…` |
| `node/Actions.tsx` | Remove (via Confirm) | `Removing…` |
| `node/Actions.tsx` | Break down | `Starting…` |
| `node/StartPicker.tsx` | each agent row: render a `Button variant="outline"` per agent instead of a `list-row` so it can show busy | `Starting…` |
| `node/Discussion.tsx`, `cards/Discuss.tsx`, `node/SpecBody.tsx` | Send / Comment | `Sending…` |
| `cards/Card.tsx` | Approve / Acknowledge / Send back (replace its local `busy`/`error` state with `useAction`) | `Approving…` / `Acknowledging…` / `Sending…` |
| `shell/ProjectMenu.tsx` | Resume; Pause's confirm handler | `Resuming…` / `Pausing…` |
| `project/CloseSheet.tsx` | Close and commit structure | `Closing…` |
| `project/ProjectPage.tsx` | ▶ Run | `Starting…` |

Each component that shows a `useAction` error renders it inline, under its buttons: `{a.error ? <div class="callout danger">{a.error}</div> : null}` (BreakdownButton already renders its error — convert it to use `a.error`).

`AddForm.save` becomes:

```tsx
  const a = useAction();
  async function save() {
    if (!title.trim()) return;
    const ok = await a.run(() => post(routes.nodeChildren(parentId), {
      title, body_md: purpose, criteria: criteria.split("\n").map((l) => l.trim()).filter(Boolean),
      depends_on: receives.map((r) => ({ id: r.id, carries: r.carries || null })), predicted_touches: [],
    }));
    if (ok) { refresh(); onClose(); }
  }
```

with `<Button type="submit" variant="filled" busy={a.busy} busyLabel="Saving…">Save</Button>` and the inline error callout above the actions row. Remove the `startCreate`/`resolveCreate`/`failCreate` import. In `canvas/Canvas.tsx`, delete `pendingCreateGhosts` and the `pendingCreates` import and the ghost `GhostBox` map that uses it (leave the breakdown ghosts for Task 6).

Do not change the texts of the non-busy labels in this task (Task 7/8 rename some).

- [ ] **Step 5: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: 0 type errors; all tests pass (update any existing test that asserted the old `…` busy text in `Card.test.tsx` to the new labels); build writes `src/muvue/api/static/index.html`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: every action button shows a spinner and its error while launching"
```

---

### Task 6: Activity bar driven by the server

Fixes D2 and D3: after any launch the page shows, within one frame, a sticky activity bar ("Starting…", then "fake is planning tasks for “voxscore v0.1” · 4s · View log", then either the new tasks or a persistent red failure with the reason and log). It survives reloads and works at every width because it reads `GET /projects/{id}/activity`.

**Files:**
- Create: `dashboard/src/project/activity.ts`, `dashboard/src/project/ActivityBar.tsx`, `dashboard/src/project/LogSheet.tsx`
- Modify: `dashboard/src/api/routes.ts`, `dashboard/src/node/Logs.tsx`, `dashboard/src/canvas/AddForm.tsx` (`BreakdownButton`), `dashboard/src/node/Actions.tsx` (`breakDown`), `dashboard/src/node/StartPicker.tsx`, `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/canvas/Canvas.tsx`, `dashboard/src/canvas/canvas.css`
- Delete: `dashboard/src/canvas/pending.ts`, `dashboard/src/canvas/GhostBox.tsx`, `dashboard/test/pending.test.ts`, `dashboard/test/GhostBox.test.tsx`
- Test: `dashboard/test/activity.test.ts` (create), `dashboard/test/ActivityBar.test.tsx` (create)

**Interfaces:**
- Consumes: `GET /projects/{id}/activity` and `GET /projects/{id}/logs` (Task 3); `useAction` (Task 5).
- Produces (`dashboard/src/project/activity.ts`):

```ts
export type ActiveProc = { kind: "run" | "breakdown"; pid: number; node_id: number | null; started_at: string };
export type BreakdownOutcome = { node_id: number; event_id: number; type: "breakdown.started" | "breakdown.finished" | "breakdown.failed"; ts: string; agent: string | null; reason: string | null; created: number[] | null };
export type Working = { node_id: number; title: string; agent: string | null };
export type Activity = { active: ActiveProc[]; breakdowns: BreakdownOutcome[]; working: Working[] };
export type Launch = { kind: "run" | "breakdown"; nodeId: number | null; label: string; afterEventId: number; at: number };
export type LogRef = { kind: "node"; nodeId: number } | { kind: "project"; projectId: number };
export type ActivityItem =
  | { tone: "busy"; key: string; text: string; startedAt: number; log: LogRef | null }
  | { tone: "error"; key: string; text: string; log: LogRef | null };

export const launches: Signal<Launch[]>;
export function markLaunched(l: Omit<Launch, "at" | "afterEventId">, activity: Activity | null): void;
export function latestEventId(activity: Activity | null, nodeId: number | null): number;
export function activityItems(a: Activity | null, launched: Launch[], now: number, titles: Record<number, string>, dismissed: Set<string>, projectId: number): ActivityItem[];
export function useActivity(projectId: number | null): Activity | null;
export function dismiss(key: string): void;
export const dismissedKeys: Signal<Set<string>>;
export const LAUNCH_TIMEOUT_MS = 15000;
```

- `planningNodeIds(a: Activity | null, launched: Launch[]): Set<number>` — node ids with a breakdown running or just launched (Task 9's placeholder reads it).

Rules for `activityItems` (in this order; all tested):
1. Each `active` entry with `kind: "breakdown"` → busy, key `bd-run:<node_id>`, text `` `✨ ${agent} is planning tasks for “${title}”` `` where `agent` is that node's `breakdowns` entry's `agent` (fallback `"agent"`) and `title` is `titles[node_id] ?? "#"+node_id`; log `{kind:"node", nodeId}`.
2. Any `active` entry with `kind: "run"` (at most one item) → busy, key `run`, text `▶ Running tasks · ${n} agent${n === 1 ? "" : "s"} working` (n = `working.length`), then, if `working` is non-empty, `` ` — ${working.map((w) => w.title).join(", ")}` ``; log `{kind:"project", projectId}`.
3. Each launch not yet confirmed → if `now - at < LAUNCH_TIMEOUT_MS`: busy, key `launch:<kind>:<nodeId>`, text `` `Starting ${label}…` ``. A breakdown launch is *confirmed* once an `active` breakdown for its node exists or that node's `breakdowns` entry has `event_id > afterEventId`. A run launch is confirmed once any `active` run exists. After the timeout, unconfirmed → error, key `launch-timeout:<kind>:<nodeId>:<at>`, text `` `${label} did not start: nothing was reported within 15 seconds. Check the log.` ``.
4. Each `breakdowns` entry with `type === "breakdown.failed"` whose key `bd-fail:<event_id>` is not in `dismissed` → error, text `` `Planning “${title}” failed: ${reason ?? "no reason recorded"}` ``, log node.
- `useActivity` fetches `routes.projectActivity(projectId)` on `[projectId, refreshTick.value]`, and while the last response has any `active` entry or any unconfirmed launch exists, refetches every 1500 ms (interval cleared when idle/unmounted). On each response it drops confirmed launches from `launches`, and when a node's outcome turns `breakdown.finished` with `event_id > afterEventId` of a launch it just confirmed, it calls `toast(`Added ${created.length} tasks`)` and `refresh()`.
- `dismissedKeys` initialises from `localStorage["muvue.dismissedActivity"]` (JSON array) inside `try/catch` (empty set on any error); `dismiss` adds a key and writes back inside `try/catch`.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/activity.test.ts`:

```ts
import { activityItems, type Activity, type Launch, LAUNCH_TIMEOUT_MS } from "../src/project/activity";

const idle: Activity = { active: [], breakdowns: [], working: [] };
const titles = { 1: "voxscore v0.1" };

test("a fresh launch shows Starting… immediately", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, label: "planning", afterEventId: 0, at: 1000 };
  const items = activityItems(idle, [l], 1100, titles, new Set(), 1);
  expect(items).toEqual([expect.objectContaining({ tone: "busy", text: "Starting planning…" })]);
});

test("a running breakdown names the agent and the node", () => {
  const a: Activity = { active: [{ kind: "breakdown", pid: 5, node_id: 1, started_at: "t" }], breakdowns: [{ node_id: 1, event_id: 8, type: "breakdown.started", ts: "t", agent: "fake", reason: null, created: null }], working: [] };
  const [item] = activityItems(a, [], 0, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "busy", text: "✨ fake is planning tasks for “voxscore v0.1”", log: { kind: "node", nodeId: 1 } });
});

test("a failed breakdown stays on screen with its reason until dismissed", () => {
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "agent produced no parseable breakdown", created: null }], working: [] };
  expect(activityItems(a, [], 0, titles, new Set(), 1)).toEqual([expect.objectContaining({ tone: "error", text: "Planning “voxscore v0.1” failed: agent produced no parseable breakdown" })]);
  expect(activityItems(a, [], 0, titles, new Set(["bd-fail:9"]), 1)).toEqual([]);
});

test("a launch that the server confirmed is not shown twice", () => {
  const l: Launch = { kind: "breakdown", nodeId: 1, label: "planning", afterEventId: 7, at: 0 };
  const a: Activity = { active: [], breakdowns: [{ node_id: 1, event_id: 9, type: "breakdown.failed", ts: "t", agent: "fake", reason: "x", created: null }], working: [] };
  const items = activityItems(a, [l], 10, titles, new Set(), 1);
  expect(items.map((i) => i.tone)).toEqual(["error"]);
});

test("an unconfirmed launch turns into an error after the timeout", () => {
  const l: Launch = { kind: "run", nodeId: null, label: "the run", afterEventId: 0, at: 0 };
  const [item] = activityItems(idle, [l], LAUNCH_TIMEOUT_MS + 1, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "error", text: "the run did not start: nothing was reported within 15 seconds. Check the log." });
});

test("a live run lists what is being worked on", () => {
  const a: Activity = { active: [{ kind: "run", pid: 3, node_id: null, started_at: "t" }], breakdowns: [], working: [{ node_id: 2, title: "Detect pitch", agent: "claude" }] };
  const [item] = activityItems(a, [], 0, titles, new Set(), 1);
  expect(item).toMatchObject({ tone: "busy", text: "▶ Running tasks · 1 agent working — Detect pitch", log: { kind: "project", projectId: 1 } });
});
```

`dashboard/test/ActivityBar.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/preact";
import { ActivityBar } from "../src/project/ActivityBar";

test("busy items show a spinner and elapsed seconds; errors can be dismissed", () => {
  const onDismiss = vi.fn();
  const { container } = render(
    <ActivityBar
      now={5000}
      items={[
        { tone: "busy", key: "a", text: "Starting planning…", startedAt: 1000, log: null },
        { tone: "error", key: "bd-fail:9", text: "Planning “v” failed: boom", log: { kind: "node", nodeId: 1 } },
      ]}
      onDismiss={onDismiss}
      onOpenLog={() => {}}
    />,
  );
  expect(container.querySelector("[data-activity]")).toBeTruthy();
  expect(container.querySelector(".spinner")).toBeTruthy();
  expect(screen.getByText("4s")).toBeTruthy();
  fireEvent.click(screen.getByText("Dismiss"));
  expect(onDismiss).toHaveBeenCalledWith("bd-fail:9");
});

test("renders nothing when idle", () => {
  const { container } = render(<ActivityBar now={0} items={[]} onDismiss={() => {}} onOpenLog={() => {}} />);
  expect(container.querySelector("[data-activity]")).toBeNull();
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/activity.test.ts test/ActivityBar.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

Add to `routes.ts`: `projectActivity: (id: number) => `/projects/${id}/activity`,` and `projectLogs: (id: number, lines = 200) => q(`/projects/${id}/logs`, { lines }),`.

`Logs.tsx`: change the props to `{ nodeId?: number; path?: string }` and fetch `path ?? routes.nodeLogs(nodeId!)`; keep everything else.

`dashboard/src/project/activity.ts` — implement exactly the interface and rules above. Key pieces:

```ts
import { signal } from "@preact/signals";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api/client";
import { routes } from "../api/routes";
import { refresh, refreshTick, toast } from "../state";

export const LAUNCH_TIMEOUT_MS = 15000;
const STORAGE_KEY = "muvue.dismissedActivity";

function loadDismissed(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as string[]); } catch { return new Set(); }
}
export const dismissedKeys = signal<Set<string>>(loadDismissed());
export function dismiss(key: string): void {
  const next = new Set(dismissedKeys.value); next.add(key); dismissedKeys.value = next;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...next])); } catch { /* per-viewer convenience only; the in-memory set still hides it */ }
}

export const launches = signal<Launch[]>([]);

export function latestEventId(a: Activity | null, nodeId: number | null): number {
  return a?.breakdowns.find((b) => b.node_id === nodeId)?.event_id ?? 0;
}
export function markLaunched(l: Omit<Launch, "at" | "afterEventId">, a: Activity | null): void {
  const launch: Launch = { ...l, at: Date.now(), afterEventId: latestEventId(a, l.nodeId) };
  launches.value = [...launches.value.filter((x) => !(x.kind === l.kind && x.nodeId === l.nodeId)), launch];
}
function confirmed(l: Launch, a: Activity | null): boolean {
  if (!a) return false;
  if (l.kind === "run") return a.active.some((p) => p.kind === "run");
  return a.active.some((p) => p.kind === "breakdown" && p.node_id === l.nodeId) || latestEventId(a, l.nodeId) > l.afterEventId;
}
```

`activityItems` builds the list per the four rules (use `confirmed` for rule 3). `planningNodeIds` returns node ids from active breakdowns plus unconfirmed, un-timed-out breakdown launches.

`useActivity(projectId)`:

```ts
export function useActivity(projectId: number | null): Activity | null {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [tick, setTick] = useState(0);
  const prev = useRef<Activity | null>(null);
  useEffect(() => {
    if (projectId === null) return;
    let alive = true;
    api<Activity>(routes.projectActivity(projectId)).then((a) => {
      if (!alive) return;
      for (const l of launches.value) {
        if (l.kind !== "breakdown" || !confirmed(l, a)) continue;
        const outcome = a.breakdowns.find((b) => b.node_id === l.nodeId);
        if (outcome?.type === "breakdown.finished" && outcome.event_id > l.afterEventId) { toast(`Added ${outcome.created?.length ?? 0} tasks`); refresh(); }
      }
      launches.value = launches.value.filter((l) => !confirmed(l, a) || (l.kind === "breakdown" && a.active.some((p) => p.node_id === l.nodeId)));
      prev.current = a;
      setActivity(a);
    }, (e) => { if (alive) toast(e instanceof Error ? e.message : String(e), "error"); });
    return () => { alive = false; };
  }, [projectId, refreshTick.value, tick]);
  const polling = !!activity && (activity.active.length > 0 || launches.value.length > 0);
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(() => setTick((n) => n + 1), 1500);
    return () => clearInterval(t);
  }, [polling]);
  return activity;
}
```

Note the filter keeps a confirmed breakdown launch while its process is still active so its "finished" transition can still trigger the toast; once the process is gone and the outcome is recorded, it is dropped. Also: an error while fetching is surfaced (toast) — the bar's content is the persistent surface; do not add a silent catch.

`dashboard/src/project/ActivityBar.tsx`:

```tsx
import type { ActivityItem, LogRef } from "./activity";
import { Button } from "../ui/Button";

export function ActivityBar({ items, now, onDismiss, onOpenLog }: { items: ActivityItem[]; now: number; onDismiss: (key: string) => void; onOpenLog: (log: LogRef) => void }) {
  if (!items.length) return null;
  return (
    <div class="activity-bar stack tight" data-activity role="status" aria-live="polite">
      {items.map((i) => (
        <div class={"activity-item " + i.tone} key={i.key}>
          {i.tone === "busy" ? <span class="spinner" aria-hidden="true" /> : <span class="activity-icon" aria-hidden="true">!</span>}
          <span class="grow">{i.text}</span>
          {i.tone === "busy" ? <span class="caption">{Math.max(0, Math.round((now - i.startedAt) / 1000))}s</span> : null}
          {i.log ? <Button variant="plain" onClick={() => onOpenLog(i.log!)}>View log</Button> : null}
          {i.tone === "error" ? <Button variant="plain" onClick={() => onDismiss(i.key)}>Dismiss</Button> : null}
        </div>
      ))}
    </div>
  );
}
```

`startedAt` for busy items: `activityItems` sets it from the launch's `at` for rule 3; for rules 1–2 it uses the first time this client saw the process — keep a module-level `Map<string, number>` `firstSeen` keyed by item key, set to `now` when absent (server `started_at` is not used because phone and server clocks differ).

`dashboard/src/project/LogSheet.tsx`:

```tsx
import { Sheet } from "../ui/Sheet";
import { Logs } from "../node/Logs";
import { routes } from "../api/routes";
import type { LogRef } from "./activity";

export function LogSheet({ log, onClose }: { log: LogRef; onClose: () => void }) {
  return (
    <Sheet title={log.kind === "node" ? `Log · #${log.nodeId}` : "Run log"} onClose={onClose}>
      <Logs path={log.kind === "node" ? routes.nodeLogs(log.nodeId) : routes.projectLogs(log.projectId)} />
    </Sheet>
  );
}
```

CSS (append to `canvas.css`):

```css
.activity-bar { position: sticky; top: 0; z-index: 5; margin: 0 16px 12px; }
@media (max-width: 899px) { .activity-bar { top: calc(61px + env(safe-area-inset-top)); } }
.activity-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: var(--radius); font-size: 14px; background: color-mix(in srgb, var(--st-in_progress) 16%, var(--surface)); border: 1px solid var(--st-in_progress); box-shadow: var(--shadow); }
.activity-item.error { background: color-mix(in srgb, var(--danger) 12%, var(--surface)); border-color: var(--danger); }
.activity-icon { width: 18px; height: 18px; border-radius: 50%; background: var(--danger); color: #fff; font-weight: 700; font-size: 12px; display: inline-flex; align-items: center; justify-content: center; flex: none; }
```

(61px is the phone top bar's height: 8+44+8+1. Measure it in the browser during Task 11 and adjust if different.)

Wire-up:
- `ProjectPage`: `const activity = useActivity(pid);` a `now` state updated every 1000 ms while `items` has a busy item; `const items = activityItems(activity, launches.value, now, titles, dismissedKeys.value, pid)` where `titles` maps graph node ids to titles; render `<ActivityBar …/>` directly below the header row; `onOpenLog` opens `<LogSheet>`; `onDismiss={dismiss}`. The Run handler, on success, calls `markLaunched({ kind: "run", nodeId: null, label: "the run" }, activity)`.
- `BreakdownButton` gets a new required prop `activity: Activity | null` and on success calls `markLaunched({ kind: "breakdown", nodeId, label: "planning" }, activity)` then `refresh()` (remove `startBreakdownGhosts`). `Actions.breakDown` does the same — `Actions` reads activity through a new optional prop `activity?: Activity | null` passed from `NodeSheet`, which calls `useActivity(detail.node.project_id)`; pass `null` when absent (launch then compares against event id 0, which is still correct for "a newer outcome appeared" because event ids only grow — it only risks treating an old outcome as confirmation, so prefer passing it).
- `StartPicker`, on success: `markLaunched({ kind: "run", nodeId, label: `#${nodeId}` }, null)`. A single-node start spawns `muvue run --node`, which registers in the runner registry as `kind: "run"`, so it is confirmed like a run.
- Remove from `ProjectPage` the ghost-clearing `useEffect` and the `pending` imports; remove from `Canvas` the `breakdownGhosts` map, `ghostY`, and the `GhostBox`/`pending` imports. Delete `pending.ts`, `GhostBox.tsx`, and their tests. Update `Canvas.test.tsx`/`ProjectPage.test.tsx` mocks to answer `/projects/1/activity` with `{ active: [], breakdowns: [], working: [] }`.

- [ ] **Step 4: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build` → all green.

- [ ] **Step 5: Commit**

```bash
git add -A dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: server-driven activity bar with persistent failures and logs"
```

---

### Task 7: Honest status words

Fixes D6 and D7.

**Files:**
- Modify: `dashboard/src/canvas/agentState.ts`, `dashboard/src/canvas/AgentChip.tsx`, `dashboard/src/project/StatusLine.tsx`, `dashboard/src/canvas/SpecRoot.tsx`, `dashboard/src/project/ProjectPage.tsx`
- Test: `dashboard/test/agentState.test.ts`, `dashboard/test/StatusLine.test.tsx`, `dashboard/test/ProjectPage.test.tsx` (update/append)

**Interfaces:**
- Produces:
  - `AgentState = "unassigned" | "waiting_for_plan_approval" | "waiting_on_earlier" | "ready" | "running" | "waiting_on_you" | "done" | "failed" | "paused"` (the old `"queued"` is gone — search the codebase for `"queued"` and replace every use).
  - `agentStateOf(node, projectPhase, needsYou)` order: no agent → `unassigned`; paused phase and status `in_progress`/`ready` → `paused`; `done` → `done`; `failed` → `failed`; `needsYou` or `review` or `awaiting_approval` → `waiting_on_you`; `in_progress` → `running`; phase `planning` → `waiting_for_plan_approval`; status `ready` → `ready`; otherwise → `waiting_on_earlier`.
  - `AgentChip` labels (exact): `unassigned: "no agent"`, `waiting_for_plan_approval: "starts after the task list is approved"`, `waiting_on_earlier: "waits for earlier tasks"`, `ready: "ready — starts on Run"`, `running: "working now"`, `waiting_on_you: "waiting on you"`, `done: "done"`, `failed: "failed"`, `paused: "paused"`. Chip text is `` `${agent} · ${label}` ``, or the label alone when there is no agent.
  - `StatusLine({ phase, done, total, working }: { phase: Phase; done: number; total: number; working: number })` renders exactly: `` `${PHASE[phase]} · ${done} of ${total} tasks done · ${working === 0 ? "no agent working" : working === 1 ? "1 agent working" : `${working} agents working`}` `` with `PHASE = { planning: "Planning", executing: "Running", paused: "Paused", closed: "Closed" }`. The spend part and its CSS classes go (they were hard-coded `$0.00 of $1.00`; `.spend-amber`/`.spend-red` are deleted from `canvas.css` if nothing else uses them — grep first).
  - `SpecRoot`'s chip becomes a plain caption `` `planned by ${spec.agent}` `` (a spec is planned, never queued); omit it when `spec.agent` is null.
  - `ProjectPage` renders, when any `/graph` node has `agent === "fake"`, a `<div class="callout" data-fake-notice>` with exactly: `Tasks here are routed to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.`

- [ ] **Step 1: Write the failing tests**

Replace the body of `dashboard/test/agentState.test.ts` with table-driven cases:

```ts
import { agentStateOf } from "../src/canvas/agentState";

test.each([
  [{ status: "ready", owner: null, agent: null }, "executing", false, "unassigned"],
  [{ status: "ready", owner: null, agent: "fake" }, "planning", false, "waiting_for_plan_approval"],
  [{ status: "pending", owner: null, agent: "fake" }, "executing", false, "waiting_on_earlier"],
  [{ status: "ready", owner: null, agent: "fake" }, "executing", false, "ready"],
  [{ status: "in_progress", owner: "fake", agent: "fake" }, "executing", false, "running"],
  [{ status: "review", owner: null, agent: "fake" }, "executing", false, "waiting_on_you"],
  [{ status: "ready", owner: null, agent: "fake" }, "paused", false, "paused"],
  [{ status: "done", owner: null, agent: "fake" }, "planning", false, "done"],
] as const)("%o in %s → %s", (node, phase, needsYou, expected) => {
  expect(agentStateOf(node, phase, needsYou)).toBe(expected);
});
```

`dashboard/test/StatusLine.test.tsx`:

```tsx
import { render } from "@testing-library/preact";
import { StatusLine } from "../src/project/StatusLine";

test("plain-language status with no invented spend", () => {
  const { container } = render(<StatusLine phase="planning" done={0} total={3} working={0} />);
  expect(container.textContent).toBe("Planning · 0 of 3 tasks done · no agent working");
  expect(container.textContent).not.toContain("$");
});
```

Append to `dashboard/test/ProjectPage.test.tsx` a test that mocks `/graph` with one spec node whose `agent` is `"fake"` and asserts `document.querySelector("[data-fake-notice]")` contains `demo agent` (copy the mock setup from the existing test in that file, and add the `/projects/1/activity` mock).

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/agentState.test.ts test/StatusLine.test.tsx test/ProjectPage.test.tsx` → FAIL.

- [ ] **Step 3: Implement** the interfaces above. `ProjectPage` computes `working` as the number of nodes with status `in_progress` (replacing `busyAgents`), and passes `phase`, `done`, `total`, `working` to `StatusLine`.

- [ ] **Step 4: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build` → green (update `AgentChip`/`TaskBox`/`SpecRoot` tests that asserted `queued`).

- [ ] **Step 5: Commit**

```bash
git add -A dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: honest status words, no invented spend, fake-agent notice"
```

---

### Task 8: One guided next step; Run says why it cannot run

Fixes D5 (frontend) and D8. A "Next" bar under the title always names the single next thing to do, with its button. `▶ Run tasks` is disabled with a visible reason whenever running would do nothing. The spec-approval and task-list-approval actions move out of the card rail and the spec box into this bar, so each appears once.

**Files:**
- Create: `dashboard/src/project/nextStep.ts`, `dashboard/src/project/NextStepBar.tsx`
- Modify: `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/canvas/SpecRoot.tsx` (remove the approve buttons, `canApproveSpec`, `canApproveTasks`, and the BreakdownButton), `dashboard/src/cards/cardsFromInbox.ts` (stop emitting `spec_review` and `gate2_review` cards), `dashboard/src/cards/Card.tsx` (drop their `APPROVE_TARGET`/`CAPTION` entries and the gate2 id special case), `dashboard/src/canvas/canvas.css`
- Test: `dashboard/test/nextStep.test.ts` (create), `dashboard/test/NextStepBar.test.tsx` (create), `dashboard/test/cardsFromInbox.test.ts`, `dashboard/test/SpecRoot.test.tsx`, `dashboard/test/Card.test.tsx` (update)

**Interfaces:**
- Consumes: `planningNodeIds` (Task 6), `useAction`/`Button busy` (Task 5), `BreakdownButton` (with the `activity` prop from Task 6).
- Produces (`nextStep.ts`):

```ts
export type NextStepInput = { phase: Phase; spec: { id: number; status: NodeStatus } | null; taskCount: number; readyCount: number; workingCount: number; reviewCount: number; doneCount: number; planning: boolean };
export type NextStepId = "closed" | "write_spec" | "approve_spec" | "planning" | "plan_tasks" | "approve_tasks" | "paused" | "running" | "review" | "run" | "all_done" | "stuck";
export type NextStep = { id: NextStepId; title: string; detail: string };
export function nextStep(i: NextStepInput): NextStep;
export function runBlockReason(step: NextStep): string | null;
```

`nextStep` — first match wins; exact strings:

| Condition | id | title | detail |
|---|---|---|---|
| `phase === "closed"` | closed | `Project closed` | `Nothing more to do here.` |
| `!spec` | write_spec | `Write the spec` | `Describe what to build, one requirement per line.` |
| `spec.status === "pending"` | approve_spec | `Approve the spec` | `Read it (tap the spec box), comment on any line, then approve so tasks can be planned.` |
| `planning` | planning | `Planning tasks…` | `An agent is splitting the spec into tasks. They appear in the diagram when it finishes.` |
| `phase === "planning" && taskCount === 0` | plan_tasks | `Plan the tasks` | `Let an agent propose tasks from the spec, or add them yourself.` |
| `phase === "planning"` | approve_tasks | `Approve the task list` | `` `Check the ${taskCount} task${taskCount === 1 ? "" : "s"} in the diagram. Approving freezes their criteria and lets Run start them.` `` |
| `phase === "paused"` | paused | `Paused` | `Resume from the project menu to continue.` |
| `workingCount > 0` | running | `Agents are working` | `` `${workingCount} task${workingCount === 1 ? " is" : "s are"} in progress. Watch the diagram or open a task for its log.` `` |
| `reviewCount > 0` | review | `Review finished work` | `` `${reviewCount} task${reviewCount === 1 ? " waits" : "s wait"} for your review in Needs you.` `` |
| `readyCount > 0` | run | `Run the tasks` | `` `${readyCount} task${readyCount === 1 ? " is" : "s are"} ready. Press ▶ Run tasks to start agents on them.` `` |
| `taskCount > 0 && doneCount === taskCount` | all_done | `All tasks done` | `Close the project from the project menu.` |
| otherwise | stuck | `Nothing can run` | `The remaining tasks are blocked or waiting on earlier tasks. Open a task to see why.` |

`runBlockReason` returns `null` only for `run`. Otherwise (exact): write_spec/approve_spec/planning/plan_tasks/approve_tasks → `Run starts after the task list is approved.`; paused → `The project is paused.`; running → `Already running.`; review → `Waiting on your review.`; all_done → `Nothing left to run.`; closed → `The project is closed.`; stuck → `Nothing is ready to run.`

`NextStepBar({ step, authed, projectId, specId, activity, onAddTask }: { step: NextStep; authed: boolean; projectId: number; specId: number | null; activity: Activity | null; onAddTask: () => void })` renders (spec-dependent buttons render only when `specId !== null`) `<section class="next-step card" data-next-step={step.id}>` with a `caption` "Next", `<h2>{step.title}</h2>`, `<p class="muted">{step.detail}</p>`, and, only when `authed`, the action for the step:
- approve_spec: `Button filled` "Approve spec" (busy "Approving…") → `post(routes.nodeApprove(specId), { target: "spec" })`, then `toast("Spec approved")`, `refresh()`.
- plan_tasks: `BreakdownButton nodeId={specId} activity={activity} label="✨ Plan tasks with agent"` and `Button outline` "+ Add task myself" → `onAddTask()`.
- approve_tasks: `Button filled` "Approve task list" (busy "Approving…") → `post(routes.nodeApprove(projectId), { target: "gate2" })` (gate2 is keyed by project id — `core.gates.approve_gate2`), then `toast("Task list approved")`, `refresh()`.
- all other steps: no button (Run lives in the header).
When `!authed` it shows `<p class="caption">Read-only. Open the link printed by muvue serve to act.</p>` instead of buttons.

`BreakdownButton` gains an optional `label?: string` prop (default `✨ Break down with agent`).

`ProjectPage` header: title, then `<div class="row">` with `+ Task` (unchanged condition) and `Button filled` labelled `▶ Run tasks` (busy "Starting…"), `disabled={!!runBlockReason(step)}`, followed by `{reason ? <span class="caption" data-run-reason>{reason}</span> : null}`. The Run click stays guarded: if the server still answers 409, the `useAction` error shows its `detail` inline.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/nextStep.test.ts`:

```ts
import { nextStep, runBlockReason, type NextStepInput } from "../src/project/nextStep";

const base: NextStepInput = { phase: "planning", spec: { id: 1, status: "ready" }, taskCount: 0, readyCount: 0, workingCount: 0, reviewCount: 0, doneCount: 0, planning: false };

test.each([
  [{ spec: null }, "write_spec"],
  [{ spec: { id: 1, status: "pending" } }, "approve_spec"],
  [{ planning: true }, "planning"],
  [{}, "plan_tasks"],
  [{ taskCount: 3 }, "approve_tasks"],
  [{ phase: "executing", taskCount: 3, readyCount: 2 }, "run"],
  [{ phase: "executing", taskCount: 3, workingCount: 1, readyCount: 2 }, "running"],
  [{ phase: "executing", taskCount: 3, reviewCount: 1 }, "review"],
  [{ phase: "executing", taskCount: 3, doneCount: 3 }, "all_done"],
  [{ phase: "executing", taskCount: 3 }, "stuck"],
  [{ phase: "paused", taskCount: 3, readyCount: 3 }, "paused"],
  [{ phase: "closed" }, "closed"],
] as const)("%o → %s", (over, id) => {
  expect(nextStep({ ...base, ...(over as Partial<NextStepInput>) }).id).toBe(id);
});

test("Run is only enabled at the run step, and says why otherwise", () => {
  expect(runBlockReason(nextStep({ ...base, phase: "executing", taskCount: 1, readyCount: 1 }))).toBeNull();
  expect(runBlockReason(nextStep({ ...base, taskCount: 3 }))).toBe("Run starts after the task list is approved.");
});

test("detail counts are pluralised", () => {
  expect(nextStep({ ...base, taskCount: 1 }).detail).toBe("Check the 1 task in the diagram. Approving freezes their criteria and lets Run start them.");
});
```

`dashboard/test/NextStepBar.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { NextStepBar } from "../src/project/NextStepBar";
import * as client from "../src/api/client";

test("approve_tasks approves gate2 by project id", async () => {
  const post = vi.spyOn(client, "post").mockResolvedValue({});
  render(<NextStepBar step={{ id: "approve_tasks", title: "Approve the task list", detail: "d" }} authed projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  fireEvent.click(screen.getByText("Approve task list"));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/nodes/7/approve", { target: "gate2" }));
});

test("read-only viewers see how to act instead of buttons", () => {
  render(<NextStepBar step={{ id: "approve_spec", title: "Approve the spec", detail: "d" }} authed={false} projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  expect(screen.queryByText("Approve spec")).toBeNull();
  expect(screen.getByText("Read-only. Open the link printed by muvue serve to act.")).toBeTruthy();
});

test("plan_tasks offers both ways to create tasks", () => {
  render(<NextStepBar step={{ id: "plan_tasks", title: "Plan the tasks", detail: "d" }} authed projectId={7} specId={1} activity={null} onAddTask={() => {}} />);
  expect(screen.getByText("✨ Plan tasks with agent")).toBeTruthy();
  expect(screen.getByText("+ Add task myself")).toBeTruthy();
});
```

In `cardsFromInbox.test.ts`, change the spec-review/gate2 tests to assert those cards are **not** produced (`expect(cards.some((c) => c.kind === "spec_review" || c.kind === "gate2_review")).toBe(false)`); in `SpecRoot.test.tsx` and `Card.test.tsx`, delete the tests of the removed buttons/kinds.

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/nextStep.test.ts test/NextStepBar.test.tsx test/cardsFromInbox.test.ts` → FAIL.

- [ ] **Step 3: Implement.** Remove `"spec_review"` and `"gate2_review"` from `CardKind` and from `Card.tsx`; the `Ctx` fields that only fed them (`spec`, `taskCount`, `projectPhase`) are removed if nothing else reads them (grep). In `ProjectPage`, compute the `NextStepInput` from `nodesQ.data` (tasks = `kind === "task"`; ready/working/review/done counts by status over tasks and subtasks for ready/working/review, tasks only for done/total) and `planning = planningNodeIds(activity, launches.value).has(spec.id)`. Render `<NextStepBar>` between the header and the activity bar. When `step.id === "write_spec"`, render the `SubmitSpecForm` (export it from `SpecRoot.tsx`) instead of the canvas. `+ Add task myself` calls `setAddingTask(true)`.

CSS (`canvas.css`):

```css
.next-step { margin: 0 16px 12px; border-left: 4px solid var(--accent); }
.next-step h2 { margin: 2px 0 4px; }
[data-run-reason] { max-width: 220px; }
```

- [ ] **Step 4: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build` → green.

- [ ] **Step 5: Commit**

```bash
git add -A dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: guided next step; Run disabled with a reason when nothing can run"
```

---

### Task 9: A real top-down DAG at every width

Fixes D9, D10, D11 (box clutter), D12. One layout, one component, every width: spec on top, tasks in ranks below it, arrows with arrowheads from the spec to each first task and along every dependency, labels sized to their text, boxes with fixed heights so they cannot overlap, fit-to-width by default. The phone list view is deleted.

**Files:**
- Rewrite: `dashboard/src/canvas/flowLayout.ts`
- Rewrite: `dashboard/src/canvas/Canvas.tsx`
- Create: `dashboard/src/canvas/DagPlaceholder.tsx`
- Modify: `dashboard/src/canvas/TaskBox.tsx`, `dashboard/src/canvas/SpecRoot.tsx`, `dashboard/src/canvas/SubtaskRow.tsx` (no behavior change; row height follows CSS), `dashboard/src/canvas/canvas.css`, `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/cards/cards.css`, `dashboard/src/shell/shell.css`
- Delete: `dashboard/src/canvas/PhoneFlow.tsx`, `dashboard/test/PhoneFlow.test.tsx`
- Test: `dashboard/test/flowLayout.test.ts` (rewrite), `dashboard/test/Canvas.test.tsx` (rewrite), `dashboard/test/TaskBox.test.tsx` (update)

**Interfaces:**
- Consumes: `planningNodeIds`, `Activity` (Task 6), `BreakdownButton` with `label` (Task 8).
- Produces (`flowLayout.ts`):

```ts
export const NODE_W = 240, SPEC_H = 112, TASK_H_BASE = 132, SUBTASK_ROW_H = 44, MAX_SUBTASK_ROWS = 3, PLACEHOLDER_H = 164, PAD = 16, GAP_X = 24, GAP_Y = 72;
export const PLACEHOLDER_ID = -1;
export function taskHeight(subtaskCount: number): number; // TASK_H_BASE + rows*SUBTASK_ROW_H; rows = min(n, 3) + (n > 3 ? 1 : 0)
export function labelWidth(text: string): number;        // Math.min(200, Math.round(text.length * 6.8) + 20)
export function truncateLabel(text: string): string;     // > 26 chars → first 25 + "…"
export type Placed = { x: number; y: number; w: number; h: number; rank: number };
export type Arrow = { from: number; to: number; kind: "spec" | "dep" | "placeholder"; path: string; label: { x: number; y: number; text: string; full: string; w: number } | null };
export type Layout = { pos: Record<number, Placed>; arrows: Arrow[]; width: number; height: number };
export function computeLayout(spec: { id: number } | null, tasks: CanvasTask[], edges: CanvasEdge[], placeholder: boolean): Layout;
```

Layout rules:
- rank(spec) = 0. rank(task) = 1 + longest chain of dependency predecessors (a task with no incoming dep is rank 1). A dependency cycle must not hang: the recursion treats a node already on the current path as rank 1 (same guard as the old `columnsOf`).
- `placeholder === true` (only passed when there are zero tasks) adds a pseudo node `PLACEHOLDER_ID` at rank 1 with height `PLACEHOLDER_H` and an arrow `kind: "placeholder"` from the spec.
- Rank order within a row: by the mean x-order of predecessors (spec counts as order 0), ties by id.
- Heights: spec `SPEC_H`, task `taskHeight(subtasks.length)`, placeholder `PLACEHOLDER_H`. Each rank's row height = max box height in the rank. `y(rank 0) = PAD`; `y(rank r+1) = y(r) + rowHeight(r) + GAP_Y`. Boxes are top-aligned in their row.
- Row width = `n * NODE_W + (n - 1) * GAP_X`. Layout inner width = max row width. Each row is centered: `x0 = PAD + (inner - rowWidth) / 2`. `width = inner + 2 * PAD`; `height = last row y + its row height + PAD`.
- Arrows go from the source's bottom-center `(x + w/2, y + h)` to the target's top-center `(x + w/2, y - 6)` (6 px short so the arrowhead marker ends at the border): `` `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2}` `` with `dy = Math.max(24, (y2 - y1) / 2)`. Arrows: spec → every rank-1 task (`kind: "spec"`); every dep edge (`kind: "dep"`); the placeholder arrow. Only dep arrows with `carries` get a label, placed at `((x1 + x2) / 2, (y1 + y2) / 2)`, `text = truncateLabel(carries)`, `full = carries`, `w = labelWidth(text)`.

Rendering contract:
- `Canvas({ data, projectId, projectPhase, needsYou, activity, onAddTask })`:
  - `const planning = data.spec ? planningNodeIds(activity, launches.value).has(data.spec.id) : false;`
  - `const lay = computeLayout(data.spec, data.tasks, data.edges, data.tasks.length === 0 && !!data.spec && data.spec.status !== "pending");`
  - viewport width measured with a `ResizeObserver` on the viewport element (guard `typeof ResizeObserver === "undefined"` → assume `lay.width`); `fit = Math.min(1, viewportWidth / lay.width)`; `zoom` state `number | null` (null = fit); `z = zoom ?? fit`.
  - Markup:

```tsx
<div class="canvas-wrap">
  <div class="canvas-viewport" ref={viewportRef} style={{ height: Math.ceil(lay.height * z) + "px", touchAction: z > fit + 0.001 ? "none" : "pan-y" }} onPointerDown={…} onPointerMove={…} onPointerUp={…} onWheel={…}>
    <div class="canvas-frame" style={{ width: lay.width + "px", height: lay.height + "px", transform: `translate(${pan.x}px, ${pan.y}px) scale(${z})` }}>
      <svg class="dag canvas-arrows" width={lay.width} height={lay.height} aria-hidden="true">
        <defs><marker id="dag-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="dag-arrowhead" /></marker></defs>
        {lay.arrows.map((a) => (
          <g key={a.from + ">" + a.to}>
            <path class={"flow-arrow " + a.kind} d={a.path} marker-end="url(#dag-arrowhead)" />
            {a.label ? (
              <g transform={`translate(${a.label.x},${a.label.y})`}>
                <title>{a.label.full}</title>
                <rect class="arrow-label-bg" x={-a.label.w / 2} y={-11} width={a.label.w} height={22} rx={11} />
                <text class="arrow-label" text-anchor="middle" dy="4">{a.label.text}</text>
              </g>
            ) : null}
          </g>
        ))}
      </svg>
      {data.spec ? <SpecRoot spec={data.spec} style={boxStyle(lay.pos[data.spec.id]!)} /> : null}
      {data.tasks.map((t) => <TaskBox key={t.id} task={t} needsYou={needsYou} projectPhase={projectPhase} style={boxStyle(lay.pos[t.id]!)} />)}
      {lay.pos[PLACEHOLDER_ID] && data.spec ? <DagPlaceholder specId={data.spec.id} planning={planning} activity={activity} onAddTask={onAddTask} style={boxStyle(lay.pos[PLACEHOLDER_ID]!)} /> : null}
    </div>
  </div>
  <div class="canvas-zoom-controls">
    <button type="button" class="icon-btn" aria-label="zoom out" onClick={() => setZoom(Math.max(0.3, z - 0.15))}>−</button>
    <button type="button" class="icon-btn" aria-label="fit to width" onClick={() => { setZoom(null); setPan({ x: 0, y: 0 }); }}>Fit</button>
    <button type="button" class="icon-btn" aria-label="zoom in" onClick={() => setZoom(Math.min(2, z + 0.15))}>+</button>
  </div>
</div>
```

  where `boxStyle(p) = { position: "absolute", left: p.x + "px", top: p.y + "px", width: p.w + "px", height: p.h + "px" }`. Pointer-drag panning (existing handlers) only runs when `z > fit`; ctrl+wheel zoom keeps working. Two-pointer pinch: track active pointers in a `Map<number, {x,y}>`; when two are down, set zoom to `startZoom * (currentDistance / startDistance)` clamped to [0.3, 2].
- `SpecRoot({ spec, style })` — compact box: `div.spec-root-card` with the `style`, `tabIndex=0`, click/Enter → `openNode(spec.id)`; contents: title (`.title.clamp-2`) + `Pill`, purpose line (`.caption.clamp-1`), `planned by …` caption (Task 7). No buttons (NextStep owns them). `SubmitSpecForm` stays exported from this file.
- `TaskBox` — delete the actions row, the `addingSubtask`/`breakingDown` state, `stopBubble`, and the AddForm/BreakdownButton imports (they move to the node sheet in Task 10). Show at most `MAX_SUBTASK_ROWS` (3) subtask rows plus `+N more`. Title uses `.clamp-2`, purpose `.clamp-1`. Status class stays `st-bar-<status>`; add class `running` when `task.status === "in_progress"`.
- `DagPlaceholder({ specId, planning, activity, onAddTask, style })` — `div.task-box.dag-empty` with the style:
  - `planning`: `<span class="spinner" />` + `Planning tasks…` title, caption `Tasks appear here when the agent finishes.`, and three `.dag-skeleton` bars.
  - otherwise: title `No tasks yet`, caption `Tasks the spec breaks into will appear here, with arrows for what each one hands to the next.`, and (only when `authed.value`) `BreakdownButton nodeId={specId} activity={activity} label="✨ Plan tasks with agent"` plus `Button variant="plain"` "+ Add task myself" → `onAddTask()`.
  - The wrapper stops click/keydown propagation (it sits inside the pannable frame).
- `ProjectPage` — delete `isPhone` and the phone/desktop branch. Layout:

```tsx
<div class="project-layout">
  <div class="project-main">
    {/* header, NextStepBar, ActivityBar, fake notice, AddForm (when adding) */}
    <div style={{ padding: "0 16px 16px" }}><Canvas … /></div>
  </div>
  <CardRail cards={cards} />
</div>
```

- CSS (`canvas.css`, replacing the old `.canvas-*`, `.phone-*`, `.parallel-label`, `.task-box-actions`, `.canvas-spec-slot` rules):

```css
.project-layout { display: flex; flex-direction: column; }
.project-layout > .card-rail, .project-layout > .card-rail-empty, .project-layout > .card-rail-pill { order: -1; }
@media (min-width: 900px) {
  .project-layout { flex-direction: row; align-items: flex-start; }
  .project-main { flex: 1; min-width: 0; }
  .project-layout > .card-rail, .project-layout > .card-rail-empty, .project-layout > .card-rail-pill { order: 0; position: sticky; top: 0; max-height: 100dvh; }
}
.canvas-wrap { position: relative; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-2); padding-bottom: 60px; }
.canvas-viewport { position: relative; width: 100%; overflow: hidden; }
.canvas-frame { position: relative; transform-origin: 0 0; }
.canvas-arrows { position: absolute; top: 0; left: 0; pointer-events: none; overflow: visible; }
.canvas-arrows path.flow-arrow { fill: none; stroke: var(--text-2); stroke-width: 2; }
.canvas-arrows path.flow-arrow.placeholder { stroke-dasharray: 6 5; }
.canvas-arrows .dag-arrowhead { fill: var(--text-2); }
.canvas-arrows .arrow-label-bg { fill: var(--surface); stroke: var(--text-2); }
.canvas-arrows .arrow-label { font-size: 12px; fill: var(--text); }
.canvas-zoom-controls { position: absolute; right: 8px; bottom: 8px; z-index: 2; display: flex; gap: 4px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 4px; }
.spec-root-card, .task-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 10px 12px; cursor: pointer; box-shadow: var(--shadow); overflow: hidden; display: flex; flex-direction: column; gap: 4px; }
.spec-root-card { border-top: 4px solid var(--accent); }
.task-box { border-left: 4px solid var(--st-pending); }
.task-box.running { box-shadow: 0 0 0 2px var(--st-in_progress), var(--shadow); }
.clamp-1, .clamp-2 { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
.clamp-1 { -webkit-line-clamp: 1; } .clamp-2 { -webkit-line-clamp: 2; }
.dag-empty { border-style: dashed; border-left-width: 1px; cursor: default; background: color-mix(in srgb, var(--surface) 70%, transparent); }
.dag-skeleton { height: 10px; border-radius: 5px; background: var(--border); animation: pulse 1.2s ease-in-out infinite; }
```

  Keep the existing `.task-box.st-bar-*`, focus-visible, `.purpose`, `.subtask-list`, `.subtask-row` (min-height 44px), `.more-row`, `.needs-you-dot`, `.agent-chip`, `@keyframes pulse`, `.status-line*`, and `.spec-*` rules. Delete `.task-box.ghost*` (GhostBox is gone).

  `TASK_H_BASE` (132) and `SPEC_H` (112) are sized for: 10px padding ×2, a 2-line title (15px × 1.4 × 2 = 42), a 1-line purpose (~17), a 44px chip, and 4px gaps. Because each box's CSS `height` is set from the layout, a wrong estimate can only clip content, never overlap boxes. Task 11 checks for clipping in screenshots and adjusts these two constants if a box's content is cut.

- `cards.css`: the rail keeps `width: 320px` on desktop; on phone it is full-width (existing media rule).
- `shell.css`: inside the `@media (min-width: 900px)` block add `.sidebar .project-btn { flex: none; }` (D12).

- [ ] **Step 1: Write the failing tests**

`dashboard/test/flowLayout.test.ts` (replace the file):

```ts
import { computeLayout, taskHeight, labelWidth, truncateLabel, NODE_W, GAP_X, PLACEHOLDER_ID, TASK_H_BASE, SUBTASK_ROW_H } from "../src/canvas/flowLayout";
import type { CanvasTask, CanvasEdge } from "../src/canvas/canvasData";

const task = (id: number, subtaskCount = 0): CanvasTask => ({
  id, title: "t" + id, status: "ready", risk_tier: "low", owner: null, agent: "claude",
  body_md: null, criteria_hash: null, block_reason: null,
  subtasks: Array.from({ length: subtaskCount }, (_, i) => ({ id: id * 100 + i, title: "s" + i, status: "ready" as const, parent_id: id })),
});
const spec = { id: 1 };
const overlaps = (a: { x: number; y: number; w: number; h: number }, b: typeof a) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("spec sits alone on top; a chain goes straight down with spec + dep arrows", () => {
  const edges: CanvasEdge[] = [{ from: 2, to: 3, carries: "audio frames" }, { from: 3, to: 4, carries: "notes" }];
  const lay = computeLayout(spec, [task(2), task(3), task(4)], edges, false);
  expect([1, 2, 3, 4].map((id) => lay.pos[id]!.rank)).toEqual([0, 1, 2, 3]);
  expect(lay.pos[2]!.y).toBeGreaterThan(lay.pos[1]!.y + lay.pos[1]!.h);
  expect(lay.arrows.map((a) => a.kind).sort()).toEqual(["dep", "dep", "spec"]);
  expect(lay.arrows.find((a) => a.kind === "dep" && a.from === 2)!.label!.text).toBe("audio frames");
});

test("parallel tasks share a row side by side and never overlap", () => {
  const lay = computeLayout(spec, [task(2, 5), task(3), task(4, 2)], [], false);
  const boxes = [1, 2, 3, 4].map((id) => lay.pos[id]!);
  expect(new Set([2, 3, 4].map((id) => lay.pos[id]!.y)).size).toBe(1);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) expect(overlaps(boxes[i]!, boxes[j]!)).toBe(false);
  expect(lay.pos[3]!.x - lay.pos[2]!.x).toBe(NODE_W + GAP_X);
  expect(lay.arrows.filter((a) => a.kind === "spec")).toHaveLength(3);
});

test("rows are centered and the layout width fits the widest row", () => {
  const lay = computeLayout(spec, [task(2), task(3)], [], false);
  const specCenter = lay.pos[1]!.x + NODE_W / 2;
  const rowCenter = (lay.pos[2]!.x + lay.pos[3]!.x + NODE_W) / 2;
  expect(specCenter).toBeCloseTo(rowCenter);
  expect(lay.width).toBe(16 * 2 + 2 * NODE_W + GAP_X);
});

test("with no tasks, a placeholder node hangs under the spec with a dashed arrow", () => {
  const lay = computeLayout(spec, [], [], true);
  expect(lay.pos[PLACEHOLDER_ID]!.rank).toBe(1);
  expect(lay.arrows).toEqual([expect.objectContaining({ from: 1, to: PLACEHOLDER_ID, kind: "placeholder" })]);
});

test("box height is fixed by subtask count, capped at 3 rows plus a +N more row", () => {
  expect(taskHeight(0)).toBe(TASK_H_BASE);
  expect(taskHeight(2)).toBe(TASK_H_BASE + 2 * SUBTASK_ROW_H);
  expect(taskHeight(4)).toBe(taskHeight(10));
});

test("labels are sized to their text and long ones are truncated", () => {
  expect(labelWidth("notes")).toBeLessThan(labelWidth("audio frames"));
  expect(truncateLabel("a".repeat(40))).toHaveLength(26);
  expect(truncateLabel("short")).toBe("short");
});

test("a dependency cycle does not hang and still ranks every node", () => {
  const lay = computeLayout(spec, [task(2), task(3)], [{ from: 2, to: 3, carries: null }, { from: 3, to: 2, carries: null }], false);
  expect(Number.isFinite(lay.pos[2]!.rank)).toBe(true);
  expect(Number.isFinite(lay.pos[3]!.rank)).toBe(true);
});
```

`dashboard/test/Canvas.test.tsx` (replace the file):

```tsx
import { render, screen } from "@testing-library/preact";
import { Canvas } from "../src/canvas/Canvas";
import type { CanvasData } from "../src/canvas/canvasData";
import { authed } from "../src/state";

const data: CanvasData = {
  spec: { id: 1, title: "Voxscore", body_md: "voice to sheet music", status: "ready", agent: "fake" },
  tasks: [
    { id: 2, title: "Record voice", status: "done", risk_tier: "low", owner: null, agent: "claude", body_md: "capture mic", criteria_hash: "x", block_reason: null, subtasks: [] },
    { id: 3, title: "Detect pitch", status: "in_progress", risk_tier: "low", owner: "claude", agent: "claude", body_md: "turn audio into notes", criteria_hash: null, block_reason: null, subtasks: [] },
  ],
  edges: [{ from: 2, to: 3, carries: "audio frames" }],
};
const props = { projectId: 1, projectPhase: "executing", needsYou: new Set<number>(), activity: null, onAddTask: () => {} };

test("renders spec and tasks as one DAG with a spec arrow, a dep arrow, and arrowheads", () => {
  const { container } = render(<Canvas data={data} {...props} />);
  expect(screen.getByText("Voxscore")).toBeTruthy();
  expect(screen.getByText("audio frames")).toBeTruthy();
  expect(container.querySelectorAll("svg.dag path.flow-arrow")).toHaveLength(2);
  expect(container.querySelector("svg.dag marker#dag-arrowhead")).toBeTruthy();
  expect(container.querySelector(".task-box.running")).toBeTruthy();
});

test("every box has an explicit height so it cannot overlap its neighbours", () => {
  const { container } = render(<Canvas data={data} {...props} />);
  for (const el of container.querySelectorAll<HTMLElement>(".task-box, .spec-root-card")) expect(el.style.height).toMatch(/px$/);
});

test("no tasks: the placeholder offers both ways to create them", () => {
  authed.value = true;
  render(<Canvas data={{ ...data, tasks: [], edges: [] }} {...props} projectPhase="planning" />);
  expect(screen.getByText("No tasks yet")).toBeTruthy();
  expect(screen.getByText("✨ Plan tasks with agent")).toBeTruthy();
  expect(screen.getByText("+ Add task myself")).toBeTruthy();
  authed.value = false;
});

test("task boxes carry no inline action buttons", () => {
  authed.value = true;
  render(<Canvas data={data} {...props} />);
  expect(screen.queryByText("+ Subtask")).toBeNull();
  expect(screen.queryByText("✨")).toBeNull();
  authed.value = false;
});
```

In `TaskBox.test.tsx`, remove tests of `+ Subtask`/`✨` and assert `screen.queryByText("+ Subtask")` is null for an authed viewer.

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/flowLayout.test.ts test/Canvas.test.tsx test/TaskBox.test.tsx` → FAIL.

- [ ] **Step 3: Implement** per the interfaces and rendering contract above. Delete `PhoneFlow.tsx`, `stepsFromColumns`, `boxHeight`, `BOX_H_BASE`, `BOX_W`, `columnsOf`'s old callers, and `dashboard/test/PhoneFlow.test.tsx`; grep for each removed name and fix every import.

- [ ] **Step 4: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build` → green.

- [ ] **Step 5: Run the UI check** (it needs Tasks 2–8 in place, which they are by now):

`node dashboard/scripts/ui-check.mjs /tmp/ui-check-task9` (env as in Task 1). Expected: all DAG checks PASS at both widths. Open the four screenshots with the Read tool and check by eye: no clipped titles, arrowheads visible, labels readable. Paste the PASS/FAIL list into the report. If any DAG check fails, fix before committing.

- [ ] **Step 6: Commit**

```bash
git add -A dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: one top-down DAG for every width; delete the phone list view"
```

---

### Task 10: Node sheet: task actions, real flow links, readable runs

Fixes the rest of D11: the actions removed from task boxes live, labelled, in the node sheet; "Flow" lists real receives-from/sends-to links; the Runs list says why a breakdown failed.

**Files:**
- Create: `dashboard/src/node/flowLinks.ts`
- Modify: `dashboard/src/node/Actions.tsx`, `dashboard/src/node/NodeSheet.tsx`, `dashboard/src/node/Runs.tsx`
- Test: `dashboard/test/flowLinks.test.ts` (create), `dashboard/test/NodeSheet.test.tsx`, `dashboard/test/Runs.test.tsx` (append)

**Interfaces:**
- Consumes: `Graph` from `canvas/canvasData.ts`; `AddForm`; `BreakdownButton` with `label` and `activity`; `useActivity` (Task 6).
- Produces:
  - `flowLinks(graph: Graph, nodeId: number): { receivesFrom: { id: number; title: string; carries: string | null }[]; sendsTo: { id: number; title: string; carries: string | null }[] }` — from `dep` edges only, titles from `graph.nodes`, sorted by id.
  - `Actions` for a node with `deleted_at === null`:
    - kind `spec`: `BreakdownButton` labelled `✨ Plan more tasks with agent`.
    - kind `task`: `BreakdownButton` labelled `✨ Split into subtasks with agent`, and `Button outline` `+ Add subtask` toggling an inline `AddForm parentId={n.id} kind="subtask" candidates={[]}`.
    - all existing approve/reject/start/remove buttons unchanged (their busy states come from Task 5).
  - `Runs` row caption: for `breakdown.failed`, `` `${actor} · ${ts} · ${reason}` `` where `reason` is `JSON.parse(payload).reason`; for `breakdown.finished`, `` `… · added ${created.length}` ``. Row titles become human words: `node.start` → `Started`, `node.done` → `Finished`, `node.fail` → `Failed`, `breakdown.started` → `Planning started`, `breakdown.finished` → `Planning finished`, `breakdown.failed` → `Planning failed`.

- [ ] **Step 1: Write the failing tests**

`dashboard/test/flowLinks.test.ts`:

```ts
import { flowLinks } from "../src/node/flowLinks";
import type { Graph } from "../src/canvas/canvasData";

const node = (id: number, title: string) => ({ id, project_id: 1, parent_id: null, kind: "task", title, status: "ready" as const, risk_tier: "low" as const, owner: null, agent: null });

test("dep edges become receives-from and sends-to links; parent edges are ignored", () => {
  const g: Graph = {
    nodes: [node(2, "Record voice"), node(3, "Detect pitch"), node(4, "Export")],
    edges: [{ from: 2, to: 3, kind: "dep", carries: "audio frames" }, { from: 3, to: 4, kind: "dep", carries: null }, { from: 1, to: 3, kind: "parent" }],
  };
  expect(flowLinks(g, 3)).toEqual({
    receivesFrom: [{ id: 2, title: "Record voice", carries: "audio frames" }],
    sendsTo: [{ id: 4, title: "Export", carries: null }],
  });
});
```

Append to `dashboard/test/Runs.test.tsx` a test whose mocked `/nodes/5/runs` returns one `breakdown.failed` event with payload `{"reason":"agent produced no parseable breakdown"}` and asserts `screen.getByText("Planning failed")` and text containing `agent produced no parseable breakdown`.

Append to `dashboard/test/NodeSheet.test.tsx` a test (authed) for a `task` node asserting `✨ Split into subtasks with agent` and `+ Add subtask` are present, and that `/graph?project_id=1` data with a dep edge renders `receives audio frames from #2 Record voice` (the existing `Flow` component's text format). Mock `/projects/1/activity` too.

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && npx vitest run test/flowLinks.test.ts test/Runs.test.tsx test/NodeSheet.test.tsx` → FAIL.

- [ ] **Step 3: Implement.** `NodeSheet` fetches `routes.graph(detail.node.project_id)` once the detail is loaded (with `useApi`, deps `[detail?.node.project_id, refreshTick.value]`) and passes `flowLinks(graph, id)` to `Flow`; it also calls `useActivity(detail?.node.project_id ?? null)` and passes the result to `Actions` as `activity`. Wrap `Flow` in `<section><h3>Flow</h3>…</section>` only when it has links.

- [ ] **Step 4: Run the dashboard checks**

Run: `cd dashboard && npm run typecheck && npm test && npm run build` → green.

- [ ] **Step 5: Commit**

```bash
git add -A dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard: node sheet gets task actions, real flow links, readable runs"
```

---

### Task 11: Acceptance, docs, release notes

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]` section at the top; create the heading if the top section is a released version), `docs/decisions.md` (append the next number after the last entry, same `NNN. **Title.**` format)
- Possibly modify: `dashboard/src/canvas/flowLayout.ts` constants, `canvas.css` `.activity-bar` phone `top` (only if the checks below show a problem)

- [ ] **Step 1: Full test suites**

Run: `uv run pytest -q` and `cd dashboard && npm run typecheck && npm test && npm run build`. Expected: all green.

- [ ] **Step 2: UI check**

Run: `node dashboard/scripts/ui-check.mjs /tmp/ui-check-final` (env as in Task 1). Expected: every check PASS, exit 0. Open every screenshot with the Read tool. Look for: clipped box text (raise `TASK_H_BASE`/`SPEC_H` by the clipped amount, rerun), the phone activity bar hidden under the top bar (adjust its `top`), labels colliding with boxes. Paste the final PASS list into the report.

- [ ] **Step 3: Failure path, by hand in the browser**

Start a scratch serve with `MUVUE_FAKE_BEHAVIOR=failed` in the environment of `uv run muvue serve <scratch repo> --port 8898` (seed the repo as `ui-check.mjs` does), open the printed link in the headless browser at 390 px (a 10-line script modelled on `ui-check.mjs`), press `✨ Plan tasks with agent`, wait 3 s, screenshot. Expected: a red activity item `Planning “voxscore v0.1” failed: fake agent scripted failure` with `View log` and `Dismiss`; `View log` opens a sheet showing the fake agent's output lines. Reload the page: the failure is still shown. Press Dismiss, reload: it stays gone. Save the screenshots next to the others and mention them in the report.

- [ ] **Step 4: CHANGELOG entry** under `## [Unreleased]`:

```markdown
### Changed
- Dashboard: every launch (planning, run, start, approve, save) shows a spinner and "…ing" label on its button, and a sticky activity bar reports what is running from the server (`GET /projects/{id}/activity`). A failed planning or run stays on screen with its reason and a log link until dismissed.
- Dashboard: a "Next" bar names the one next step (write the spec, approve it, plan tasks, approve the task list, run, review, close). `▶ Run tasks` is disabled with the reason whenever running would do nothing, and `POST /projects/{id}/run` now refuses with 409 and that reason.
- Dashboard: the plan is a top-down diagram at every screen width: spec on top, arrows with arrowheads to each task, labelled with what each task hands to the next. The separate phone list view is gone. With no tasks, a placeholder under the spec offers planning with an agent or adding tasks by hand.
- Dashboard: status words say what is true ("starts after the task list is approved", "waits for earlier tasks", "ready — starts on Run"); the always-zero spend figure is gone; projects routed to the `fake` agent say it is a demo agent that writes no code.
- The `fake` agent's default behavior now answers breakdown briefs, so a freshly initialised project can be planned from the dashboard.
- Breakdown logs are served by `GET /nodes/{id}/logs`; the whole-project run log by `GET /projects/{id}/logs`.
```

- [ ] **Step 5: Decision entry** in `docs/decisions.md` (next number):

```markdown
NNN. **The dashboard reads launch progress from the server, not from its own clicks.** The canvas used to draw "breakdown in progress" ghost boxes from a client-side signal set when the button was pressed. It never saw `breakdown.failed`, so a failed breakdown spun forever (or vanished at once when tasks already existed), and phones never showed it at all. `GET /projects/{id}/activity` now reports live runner processes (the runner registry records `kind` and `node_id`) and each node's latest breakdown outcome; the dashboard only keeps a short-lived "Starting…" entry between the click and the server confirming it, and turns an unconfirmed launch into a visible error after 15 seconds. Cost if wrong: one extra small request every 1.5 s while something is running.
```

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md docs/decisions.md dashboard/src src/muvue/api/static/index.html
git commit -m "Docs: changelog and decision for dashboard clarity"
```

(Include `dashboard/src` and the bundle only if Step 2 changed constants or CSS.)
