import { readFileSync } from "node:fs";
import { join } from "node:path";

// WCAG AA: text needs 4.5:1 against what it sits on. Colours are read from
// tokens.css, so a future palette tweak that breaks a pair fails here.
const css = readFileSync(join(__dirname, "..", "src", "styles", "tokens.css"), "utf8");
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9_-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}
const split = css.indexOf("@media (prefers-color-scheme: dark)");
const light = tokens(css.slice(0, split));
const dark = { ...light, ...tokens(css.slice(split)) };

function rgb(hex: string): number[] { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function toHex(c: number[]): string { return "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join(""); }
// Same arithmetic as CSS `color-mix(in srgb, a <pct>, b)`.
function mix(a: string, pct: number, b: string): string { const x = rgb(a), y = rgb(b); return toHex(x.map((v, i) => v * pct + y[i]! * (1 - pct))); }
function lum(hex: string): number {
  const w = [0.2126, 0.7152, 0.0722];
  return rgb(hex).reduce((s, v, i) => { const c = v / 255; return s + w[i]! * (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4); }, 0);
}
function ratio(a: string, b: string): number { const [hi, lo] = [lum(a), lum(b)].sort((p, q) => q - p); return (hi! + 0.05) / (lo! + 0.05); }

const read = (...p: string[]) => readFileSync(join(__dirname, "..", "src", ...p), "utf8");
const uiCss = read("ui", "ui.css");
const baseCss = read("styles", "base.css");
const shellCss = read("shell", "shell.css");
const canvasCss = read("canvas", "canvas.css");


// Resolves `var(--x)` or `color-mix(in srgb, <c> N%, <c>)` against a token map.
function resolve(expr: string, t: Record<string, string>): string {
  if (/^#[0-9a-fA-F]{6}$/.test(expr.trim())) return expr.trim();
  const v = expr.trim().match(/^var\(--([a-z0-9_-]+)\)$/);
  if (v) { const c = t[v[1]!]; if (!c) throw new Error(`unknown token --${v[1]} in "${expr}"`); return c; }
  const m = expr.trim().match(/^color-mix\(in srgb,\s*(var\(--[a-z0-9_-]+\)|#[0-9a-fA-F]{6})\s+(\d+)%,\s*(var\(--[a-z0-9_-]+\)|#[0-9a-fA-F]{6})\)$/);
  if (!m) throw new Error(`cannot evaluate colour expression "${expr}"`);
  return mix(resolve(m[1]!, t), Number(m[2]) / 100, resolve(m[3]!, t));
}
function escapeRe(x: string): string { return x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
// Declaration value of `prop` in the rule whose selector list contains `selector`.
function decl(css: string, selector: string, prop: string): string {
  const has = (body: string) => new RegExp(`(?:^|;|\\s)${prop}:\\s*([^;]+);`).exec(body);
  const rule = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((m) => m[1]!.split(",").some((s) => s.trim().endsWith(selector)) && has(m[2]!));
  if (!rule) throw new Error(`no rule for ${selector} with ${prop}`);
  return has(rule[2]!)![1]!;
}

// One entry per .pill rule in ui.css, with the colours it really computes.
const pillRules = [...uiCss.matchAll(/(\.pill(?:\.st-[a-z_]+)?(?:,\s*\.pill\.st-[a-z_]+)*)\s*\{([^}]*)\}/g)];
const STATUSES = ["pending", "ready", "in_progress", "review", "awaiting_approval", "done", "blocked", "failed"];
function pillFor(status: string): string {
  const sel = status === "pending" ? /^\.pill$/ : new RegExp(`\\.pill\\.st-${status}(,|$)`);
  const rule = pillRules.find((m) => sel.test(m[1]!.replace(/\s+/g, "")));
  if (!rule) throw new Error(`no .pill rule for ${status}`);
  return rule[2]!;
}
function pillColours(body: string, t: Record<string, string>): [string, string] {
  return [resolve(body.match(/(?:^|[;\s])color:\s*([^;]+);/)![1]!, t), resolve(body.match(/background:\s*([^;]+);/)![1]!, t)];
}

test("all 9 .pill rules in ui.css are read by this test", () => {
  expect(pillRules).toHaveLength(9);
  for (const s of [...STATUSES, "executing", "paused", "planning"]) expect(() => pillFor(s)).not.toThrow();
});

describe.each([["light", light], ["dark", dark]] as const)("%s mode: text contrast is at least 4.5:1", (_mode, t) => {
  const surfaces: [string, string][] = [["page", t["bg"]!], ["surface", t["surface"]!], ["surface-2", t["surface-2"]!]];
  const pairs: [string, string, string][] = [
    ["body text on the page", t["text"]!, t["bg"]!],
    ["secondary text on the page", t["text-2"]!, t["bg"]!],
    ["secondary text on surface-2 (callouts, disabled buttons)", t["text-2"]!, t["surface-2"]!],
    ["filled button label", t["on-accent"]!, t["accent-strong"]!],
    ["filled button label on hover", t["on-accent"]!, t["accent-strong-hover"]!],
    ["links and plain buttons on the page", t["accent-strong"]!, t["bg"]!],
    ["links and plain buttons on a surface", t["accent-strong"]!, t["surface"]!],
    ["selected sidebar item", resolve(decl(shellCss, ".nav-item.on", "color"), t), resolve(decl(shellCss, ".nav-item.on", "background"), t)],
    ...[...STATUSES, "executing", "paused", "planning"].map((s): [string, string, string] => { const [fg, bg] = pillColours(pillFor(s), t); return [`${s} pill`, fg, bg]; }),
    ["blocked reason line on the blocked task box", resolve(decl(canvasCss, ".task-box .reason", "color"), t), resolve(decl(canvasCss, ".task-box.st-bar-blocked", "background"), t)],
    ["danger button hover", resolve(decl(uiCss, ".btn-danger", "color"), t), resolve(decl(uiCss, ".btn-danger:hover:not(:disabled)", "background"), t)],
    ["error toast label", resolve(decl(uiCss, ".toast.error", "color"), t), resolve(decl(uiCss, ".toast.error", "background"), t)],
    ...surfaces.flatMap(([name, bg]): [string, string, string][] => [
      [`danger button on ${name}`, resolve(decl(uiCss, ".btn-danger", "color"), t), bg],
      [`.tier-high on ${name}`, resolve(decl(baseCss, ".tier-high", "color"), t), bg],
      [`.tier-medium on ${name}`, resolve(decl(baseCss, ".tier-medium", "color"), t), bg],
      [`.d-add on ${name}`, resolve(decl(baseCss, ".d-add", "color"), t), bg],
      [`.d-del on ${name}`, resolve(decl(baseCss, ".d-del", "color"), t), bg],
      [`.d-hunk on ${name}`, resolve(decl(baseCss, ".d-hunk", "color"), t), bg],
    ]),
  ];
  test.each(pairs)("%s", (_name, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

test("the disabled button style is not a faded primary", () => {
  const ui = uiCss;
  expect(ui).not.toMatch(/\.btn:disabled\s*\{[^}]*opacity:\s*\.5/);
  expect(ui).toMatch(/\.btn:disabled:not\(\.busy\)\s*\{[^}]*background:\s*var\(--surface-2\)[^}]*color:\s*var\(--text-2\)/);
});

describe.each([["light", light], ["dark", dark]] as const)("%s mode: graphics contrast is at least 3:1", (_mode, t) => {
  test.each([
    ["working-now dot on a surface", t["st-in_progress-strong"]!, t["surface"]!],
    ["arrow line on the canvas", t["text-2"]!, t["surface-2"]!],
  ])("%s", (_n, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(3);
  });
});
