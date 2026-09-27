import { routes } from "./routes";
import { refresh } from "../state";

// The daemon sends one SSE message per DB change. Pages refetch on the
// refresh tick; an open sheet refetches its own node instead of closing.
export function connectStream(): void {
  if (typeof EventSource === "undefined") return; // manual refresh still works
  const src = new EventSource(routes.eventsStream());
  src.onmessage = () => refresh();
}
