import { Sheet } from "../ui/Sheet";
export function CloseSheet({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  return <Sheet title={`Close project #${projectId}`} onClose={onClose}><p class="muted">close preview arrives in Task 14</p></Sheet>;
}
