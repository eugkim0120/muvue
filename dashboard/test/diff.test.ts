import { classifyDiffLine } from "../src/node/diff";

test("diff lines classify", () => {
  expect(classifyDiffLine("diff --git a b")).toBe("d-meta");
  expect(classifyDiffLine("+++ b/x")).toBe("d-meta");
  expect(classifyDiffLine("@@ -1 +1 @@")).toBe("d-hunk");
  expect(classifyDiffLine("+added")).toBe("d-add");
  expect(classifyDiffLine("-removed")).toBe("d-del");
  expect(classifyDiffLine(" context")).toBe("");
});
