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

const STATUSES = ["pending", "ready", "in_progress", "review", "awaiting_approval", "done", "blocked", "failed"];

describe.each([["light", light], ["dark", dark]] as const)("%s mode: text contrast is at least 4.5:1", (_mode, t) => {
  const pairs: [string, string, string][] = [
    ["body text on the page", t["text"]!, t["bg"]!],
    ["secondary text on the page", t["text-2"]!, t["bg"]!],
    ["secondary text on surface-2 (callouts, disabled buttons)", t["text-2"]!, t["surface-2"]!],
    ["filled button label", t["on-accent"]!, t["accent-strong"]!],
    ["filled button label on hover", t["on-accent"]!, t["accent-strong-hover"]!],
    ["links and plain buttons on the page", t["accent-strong"]!, t["bg"]!],
    ["links and plain buttons on a surface", t["accent-strong"]!, t["surface"]!],
    ["selected sidebar item", t["accent-strong"]!, mix(t["accent"]!, 0.12, t["surface"]!)],
    ...STATUSES.map((s): [string, string, string] => [`${s} pill`, mix(t["st-" + s]!, 0.5, t["text"]!), mix(t["st-" + s]!, 0.14, t["surface"]!)]),
  ];
  test.each(pairs)("%s", (_name, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

test("the disabled button style is not a faded primary", () => {
  const ui = readFileSync(join(__dirname, "..", "src", "ui", "ui.css"), "utf8");
  expect(ui).not.toMatch(/\.btn:disabled\s*\{[^}]*opacity:\s*\.5/);
  expect(ui).toMatch(/\.btn:disabled:not\(\.busy\)\s*\{[^}]*background:\s*var\(--surface-2\)[^}]*color:\s*var\(--text-2\)/);
});
