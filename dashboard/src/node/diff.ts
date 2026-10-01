export function classifyDiffLine(line: string): "d-meta" | "d-hunk" | "d-add" | "d-del" | "" {
  if (/^(\+\+\+|---|diff |commit |index )/.test(line)) return "d-meta";
  if (line.startsWith("@@")) return "d-hunk";
  if (line.startsWith("+")) return "d-add";
  if (line.startsWith("-")) return "d-del";
  return "";
}

export function diffStat(text: string): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const line of text.split("\n")) {
    if (/^(\+\+\+|---)/.test(line)) continue;
    if (line.startsWith("+")) added++;
    else if (line.startsWith("-")) removed++;
  }
  return { added, removed };
}
