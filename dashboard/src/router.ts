import { signal } from "@preact/signals";

export type Route = { page: string; params: string[]; query: URLSearchParams };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart = "", queryPart = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  if (!parts.length || parts[0]!.startsWith("n=")) return { page: "", params: [], query: new URLSearchParams(queryPart) };
  return { page: parts[0]!, params: parts.slice(1), query: new URLSearchParams(queryPart) };
}

export const route = signal<Route>(parseHash(typeof window === "undefined" ? "" : window.location.hash));

export function resetRouteFromLocation(): void { route.value = parseHash(window.location.hash); }

if (typeof window !== "undefined") window.addEventListener("hashchange", resetRouteFromLocation);

export function navigate(hash: string): void { window.location.hash = hash; }

export function openProject(id: number): void { navigate("#/p/" + id); }

export function openNode(id: number): void {
  const r = route.value;
  const q = new URLSearchParams(r.query);
  q.set("node", String(id));
  navigate("#/" + [r.page, ...r.params].join("/") + "?" + q.toString());
}

export function closeNode(): void {
  const r = route.value;
  const q = new URLSearchParams(r.query);
  q.delete("node");
  const qs = q.toString();
  navigate("#/" + [r.page, ...r.params].join("/") + (qs ? "?" + qs : ""));
}

// The tab pages this replaces all folded into one project canvas; a link
// to any of them (bookmarked, or from history) lands on that project's
// canvas instead of a 404. "#/spec/:id" is the one case that names a
// node rather than a project — the caller resolves its project first.
const LEGACY_PAGES = new Set(["plan", "inbox", "activity", "spend"]);
export function isLegacyRoute(r: Route): boolean { return LEGACY_PAGES.has(r.page) || r.page === "spec"; }
