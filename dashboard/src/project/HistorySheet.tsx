import { Sheet } from "../ui/Sheet";
import { Timeline } from "../activity/Timeline";

export function HistorySheet({ onClose }: { onClose: () => void }) {
  return <Sheet title="History" onClose={onClose}><Timeline /></Sheet>;
}
