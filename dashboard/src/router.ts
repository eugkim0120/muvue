import { signal } from "@preact/signals";

export type Route = { page: string; params: string[]; query: URLSearchParams };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart = "", queryPart = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  // "#n=<nonce>" is the one-time login link, consumed before the router runs.
  if (!parts.length || parts[0]!.startsWith("n=")) return { page: "plan", params: [], query: new URLSearchParams(queryPart) };
  return { page: parts[0]!, params: parts.slice(1), query: new URLSearchParams(queryPart) };
}

export const route = signal<Route>(parseHash(typeof window === "undefined" ? "" : window.location.hash));

export function resetRouteFromLocation(): void { route.value = parseHash(window.location.hash); }

if (typeof window !== "undefined") window.addEventListener("hashchange", resetRouteFromLocation);

export function navigate(hash: string): void { window.location.hash = hash; }

function currentPath(): string {
  const r = parseHash(window.location.hash);
  return "#/" + [r.page, ...r.params].join("/");
}

export function openNode(id: number): void {
  const r = parseHash(window.location.hash);
  r.query.set("node", String(id));
  navigate(currentPath() + "?" + r.query.toString());
}

export function closeNode(): void {
  const r = parseHash(window.location.hash);
  r.query.delete("node");
  const q = r.query.toString();
  navigate(currentPath() + (q ? "?" + q : ""));
}
