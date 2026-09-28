import type { ComponentChildren } from "preact";

type Props = {
  variant?: "filled" | "outline" | "danger" | "plain";
  onClick?: (e: MouseEvent) => void;
  disabled?: boolean;
  busy?: boolean;
  busyLabel?: string;
  type?: "button" | "submit";
  children: ComponentChildren;
};

export function Button({ variant = "outline", onClick, disabled, busy, busyLabel, type = "button", children }: Props) {
  return (
    <button type={type} class={"btn btn-" + variant + (busy ? " busy" : "")} onClick={onClick} disabled={disabled || busy} aria-busy={busy ? "true" : undefined}>
      {busy ? <span class="spinner" aria-hidden="true" /> : null}
      {busy ? busyLabel ?? children : children}
    </button>
  );
}
