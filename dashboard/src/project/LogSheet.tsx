import { Sheet } from "../ui/Sheet";
import { Logs } from "../node/Logs";
import { routes } from "../api/routes";
import type { LogRef } from "./activity";

export function LogSheet({ log, onClose }: { log: LogRef; onClose: () => void }) {
  return (
    <Sheet title={log.kind === "node" ? `Log · #${log.nodeId}` : "Run log"} onClose={onClose}>
      <Logs path={log.kind === "node" ? routes.nodeLogs(log.nodeId) : routes.projectLogs(log.projectId)} />
    </Sheet>
  );
}
