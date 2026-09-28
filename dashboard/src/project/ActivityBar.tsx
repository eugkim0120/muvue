import type { ActivityItem, LogRef } from "./activity";
import { Button } from "../ui/Button";

export function ActivityBar({ items, now, onDismiss, onOpenLog }: { items: ActivityItem[]; now: number; onDismiss: (key: string) => void; onOpenLog: (log: LogRef) => void }) {
  if (!items.length) return null;
  return (
    <div class="activity-bar stack tight" data-activity role="status" aria-live="polite">
      {items.map((i) => (
        <div class={"activity-item " + i.tone} key={i.key}>
          {i.tone === "busy" ? <span class="spinner" aria-hidden="true" /> : <span class="activity-icon" aria-hidden="true">!</span>}
          <span class="grow">{i.text}</span>
          {i.tone === "busy" ? <span class="caption">{Math.max(0, Math.round((now - i.startedAt) / 1000))}s</span> : null}
          {i.log ? <Button variant="plain" onClick={() => onOpenLog(i.log!)}>View log</Button> : null}
          {i.tone === "error" ? <Button variant="plain" onClick={() => onDismiss(i.key)}>Dismiss</Button> : null}
        </div>
      ))}
    </div>
  );
}
