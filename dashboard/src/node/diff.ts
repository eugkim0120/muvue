export function classifyDiffLine(line: string): "d-meta" | "d-hunk" | "d-add" | "d-del" | "" {
  if (/^(\+\+\+|---|diff |commit |index )/.test(line)) return "d-meta";
  if (line.startsWith("@@")) return "d-hunk";
  if (line.startsWith("+")) return "d-add";
  if (line.startsWith("-")) return "d-del";
  return "";
}
