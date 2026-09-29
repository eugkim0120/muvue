import { readFileSync } from "node:fs";
import { join } from "node:path";

const read = (...p: string[]) => readFileSync(join(__dirname, "..", "src", ...p), "utf8");

test("wide screens put the Next step, activity and cards in a right rail beside the diagram", () => {
  const css = read("canvas", "canvas.css");
  expect(css).toMatch(/@media \(min-width: 1100px\)\s*\{[^@]*\.project-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+340px/);
  expect(css).toMatch(/grid-template-areas:\s*"head rail"\s*"dag rail"/);
});

test("phones do not repeat the project title the sticky top bar already shows", () => {
  const css = read("canvas", "canvas.css");
  expect(css).toMatch(/@media \(max-width: 899px\)\s*\{[^}]*\.project-head \.page-title,\s*\.project-menu-btn\s*\{\s*display:\s*none/);
});

test("toasts sit off the diagram: top on phones, bottom-right on desktop", () => {
  const css = read("ui", "ui.css");
  expect(css).toMatch(/\.toasts\s*\{[^}]*top:\s*calc\(env\(safe-area-inset-top\) \+ 68px\)/);
  expect(css).toMatch(/@media \(min-width: 900px\)\s*\{\s*\.toasts\s*\{[^}]*right:\s*20px/);
});
