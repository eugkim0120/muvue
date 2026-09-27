import type { ComponentChildren } from "preact";
import { Sheet } from "./Sheet";
import { Button } from "./Button";

type Props = { title: string; body: ComponentChildren; confirmLabel: string; danger?: boolean; onConfirm: () => void; onCancel: () => void };

export function Confirm({ title, body, confirmLabel, danger, onConfirm, onCancel }: Props) {
  return (
    <Sheet title={title} onClose={onCancel}>
      <div class="stack">
        <div>{body}</div>
        <div class="row">
          <Button variant={danger ? "danger" : "filled"} onClick={onConfirm}>{confirmLabel}</Button>
          <Button variant="plain" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </Sheet>
  );
}
