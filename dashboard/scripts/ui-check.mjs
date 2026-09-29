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
  const placeholderFits = await page.locator(".dag-empty").evaluate((e) => e.scrollHeight <= e.clientHeight + 1);
  check("phone: the empty-state placeholder's text fits", placeholderFits);
  const runReason = page.locator("[data-run-reason]");
  check("phone: Run explains why it is disabled", (await runReason.count()) > 0 && /approve/i.test(await runReason.first().innerText()));
  check("phone: no horizontal page scroll", await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth));

  await page.getByText("✨ Plan tasks with agent").first().click().catch(() => {});
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
  const overflowing = await page.locator(".task-box, .spec-root-card").evaluateAll((els) => els.filter((e) => e.scrollHeight > e.clientHeight + 1).map((e) => `${e.querySelector(".title")?.textContent ?? "?"}: needs ${e.scrollHeight}px, has ${e.clientHeight}px`));
  check(`${label}: every box's content fits (nothing clipped)`, overflowing.length === 0, overflowing.join("; "));
}

console.log(`\nscreenshots: ${outDir}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
