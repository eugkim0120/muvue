import { readFileSync } from "node:fs";
import { join } from "node:path";

// The 44px Global Constraint: every tappable control must have at least a
// 44px touch target. This checks the rules that were previously under it.
function minHeightOf(css: string, selector: string): number {
  const re = new RegExp(selector.replace(/[.[\]]/g, "\\$&") + "\\s*\\{([^}]*)\\}");
  const m = re.exec(css);
  if (!m) throw new Error(`selector ${selector} not found`);
  const mh = /min-height:\s*(\d+)px/.exec(m[1]!);
  if (!mh) throw new Error(`no min-height on ${selector}`);
  return Number(mh[1]);
}

test(".chip meets the 44px touch-target minimum", () => {
  const css = readFileSync(join(__dirname, "..", "src", "styles", "base.css"), "utf8");
  expect(minHeightOf(css, ".chip")).toBeGreaterThanOrEqual(44);
});

test(".spec-line meets the 44px touch-target minimum and centers its content", () => {
  const css = readFileSync(join(__dirname, "..", "src", "canvas", "canvas.css"), "utf8");
  expect(minHeightOf(css, ".spec-line")).toBeGreaterThanOrEqual(44);
  const re = /\.spec-line\s*\{([^}]*)\}/;
  const body = re.exec(css)![1]!;
  expect(body).toMatch(/align-items:\s*center/);
});
