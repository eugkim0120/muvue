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
