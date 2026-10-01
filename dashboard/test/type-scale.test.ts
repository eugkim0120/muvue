import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : []));
}
const src = join(__dirname, "..", "src");

test("no font-size in any stylesheet is below 12px", () => {
  const small: string[] = [];
  for (const f of cssFiles(src)) for (const m of readFileSync(f, "utf8").matchAll(/font-size:\s*([\d.]+)px/g)) if (Number(m[1]) < 12) small.push(`${f}: ${m[0]}`);
  expect(small).toEqual([]);
});

test("secondary text (.caption) is at least 13px", () => {
  const base = readFileSync(join(src, "styles", "base.css"), "utf8");
  const m = base.match(/\.caption\s*\{[^}]*font-size:\s*(\d+)px/);
  expect(Number(m![1])).toBeGreaterThanOrEqual(13);
});
