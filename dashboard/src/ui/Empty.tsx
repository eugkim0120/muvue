import { Icon } from "./Icon";

export function Empty({ text, check }: { text: string; check?: boolean }) {
  return (
    <div class="empty">
      {check ? <Icon name="check" /> : null}
      <div>{text}</div>
    </div>
  );
}
