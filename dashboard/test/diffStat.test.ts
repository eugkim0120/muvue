import { diffStat } from "../src/node/diff";

test("counts added and removed lines, ignoring file headers", () => {
  const text = ["commit abc", "diff --git a/x b/x", "--- a/x", "+++ b/x", "@@ -1,2 +1,3 @@", " keep", "-old", "+new", "+newer"].join("\n");
  expect(diffStat(text)).toEqual({ added: 2, removed: 1 });
});

test("empty diff is zero", () => {
  expect(diffStat("")).toEqual({ added: 0, removed: 0 });
});
