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
// Toasts currently on screen, and whether any of them covers a diagram box or
// an arrow label.
function toastOverlap(page) {
  return page.evaluate(() => {
    const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const boxes = [...document.querySelectorAll(".task-box, .spec-root-card, .arrow-label-bg")].map((e) => e.getBoundingClientRect());
    const toasts = [...document.querySelectorAll(".toast")];
    return { seen: toasts.length, covers: toasts.some((t) => boxes.some((b) => hit(t.getBoundingClientRect(), b))) };
  });
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
const { link, token } = await new Promise((res, rej) => {
  let buf = "";
  const t = setTimeout(() => rej(new Error("serve printed no link in 30s:\n" + buf)), 30000);
  const on = (d) => {
    buf += d;
    const m = buf.match(/http:\/\/\S+#n=\S+/);
    const k = buf.match(/api token: (\S+)/);
    if (m && k) { clearTimeout(t); res({ link: m[0], token: k[1] }); }
  };
  serve.stdout.on("data", on); serve.stderr.on("data", on);
});
const base = link.split("#")[0];

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
  const feedback = await page.locator("[data-activity], .task-box:not(.dag-empty)").first().isVisible();
  check("phone: within 300ms of launching, the activity bar or the new tasks are showing", feedback);

  await page.waitForFunction(() => document.querySelectorAll(".task-box").length >= 3, null, { timeout: 20000 }).catch(() => {});
  await page.waitForSelector(".toast", { timeout: 5000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: join(outDir, "03-phone-after-plan.png"), fullPage: true });
  await dagChecks(page, "phone");
  const dagTopPhone = await page.evaluate(() => document.querySelector(".canvas-wrap").getBoundingClientRect().top + window.scrollY);
  // Was 667px before this plan. With a header, the demo notice and the Next
  // card still above it, the top half of the screen is the honest target.
  check("phone: the diagram starts in the top half of the screen", dagTopPhone < 0.5 * 844, `starts at ${Math.round(dagTopPhone)}px`);
  const primaries = await page.locator(".btn-filled").evaluateAll((els) => els.filter((b) => !b.disabled && b.offsetParent !== null).length);
  check("phone: at most one enabled primary button", primaries <= 1, `${primaries} found`);
  const phoneToast = await toastOverlap(page);
  check("phone: a toast was on screen to test (the 'Added 3 tasks' one)", phoneToast.seen > 0, `${phoneToast.seen} toasts`);
  check("phone: no toast covers a box or an arrow label", !phoneToast.covers);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: join(outDir, "04-desktop-after-resize.png") });
  await dagChecks(page, "desktop (resized, no reload)");
  const desk = await page.evaluate(() => {
    const dag = document.querySelector(".canvas-wrap").getBoundingClientRect();
    const next = document.querySelector("[data-next-step]").getBoundingClientRect();
    return { dagTop: dag.top + window.scrollY, nextBeside: next.left >= dag.right - 1 };
  });
  check("desktop: the diagram starts above 200px", desk.dagTop < 200, `starts at ${Math.round(desk.dagTop)}px`);
  check("desktop: the Next step sits beside the diagram, not above it", desk.nextBeside);
  check("desktop: no horizontal page scroll", await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth));

  // --- sign-in: a visitor without the link
  const guest = await browser.newContext({ deviceScaleFactor: 2 });
  const g = await guest.newPage();
  await g.setViewportSize({ width: 390, height: 844 });
  await g.goto(base);
  await g.waitForSelector('[data-auth-strip="read_only"]', { timeout: 5000 });
  await g.waitForSelector(".task-box", { timeout: 5000 });
  await g.waitForTimeout(500);
  await g.screenshot({ path: join(outDir, "05-phone-read-only.png"), fullPage: true });
  check("read-only: no error toast on a fresh guest load", (await g.locator(".toast").count()) === 0);
  check("read-only: a one-line sign-in strip, no token form on the page", (await g.locator('[data-auth-strip="read_only"]').count()) === 1 && (await g.locator('input[aria-label="api token"]').count()) === 0);
  await g.locator("[data-auth-strip] button", { hasText: "Sign in" }).click();
  await g.locator('input[aria-label="api token"]').fill("not-the-token");
  await g.getByText("Use token").click();
  await g.waitForSelector("[data-sign-in-error]", { timeout: 3000 });
  await g.screenshot({ path: join(outDir, "06-phone-sign-in-refused.png"), fullPage: true });
  check("sign-in: a wrong token says it was not accepted", /not accepted/.test(await g.locator("[data-sign-in-error]").innerText().catch(() => "")));
  await g.locator('input[aria-label="api token"]').fill(token);
  await g.getByText("Use token").click();
  const signedIn = (pg) => pg.waitForFunction(() => !document.querySelector("[data-auth-strip]") && document.querySelector("[data-next-step]") && document.querySelectorAll(".task-box").length > 0, null, { timeout: 5000 }).then(() => true, () => false);
  check("sign-in: the printed token signs in (strip gone, project and tasks showing)", await signedIn(g));
  await g.reload();
  check("sign-in: still signed in after a reload (cookie set from the pasted token)", await signedIn(g));
  await guest.close();

  // --- muvue link: a fresh one-time link from the running daemon
  const linked = muvue("link", repo).match(/http:\/\/\S+#n=\S+/)?.[0];
  check("muvue link: prints a fresh one-time link", !!linked);
  if (linked) {
    const fresh = await browser.newContext();
    const f = await fresh.newPage();
    await f.goto(linked);
    check("muvue link: the link signs a new browser in", await signedIn(f));
    await f.setViewportSize({ width: 1280, height: 800 });
    await f.screenshot({ path: join(outDir, "07-desktop-signed-in.png") });
    await fresh.close();
  }
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
  const deadSpace = await page.locator(".task-box:not(.dag-empty)").evaluateAll((els) => els.map((e) => {
    const tag = e.querySelector(".agent-tag");
    const prev = tag?.previousElementSibling;
    const gap = tag && prev ? tag.getBoundingClientRect().top - prev.getBoundingClientRect().bottom : 0;
    return { title: e.querySelector(".title")?.textContent ?? "?", gap: Math.round(gap) };
  }).filter((b) => b.gap > 20).map((b) => `${b.title}: ${b.gap}px`));
  check(`${label}: no task box has dead space above its agent tag`, deadSpace.length === 0, deadSpace.join("; "));
}

console.log(`\nscreenshots: ${outDir}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
