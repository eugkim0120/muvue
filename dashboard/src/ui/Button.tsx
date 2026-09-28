import type { ComponentChildren } from "preact";

type Props = {
  variant?: "filled" | "outline" | "danger" | "plain";
  onClick?: (e: MouseEvent) => void;
  disabled?: boolean;
  type?: "button" | "submit";
  children: ComponentChildren;
};

export function Button({ variant = "outline", onClick, disabled, type = "button", children }: Props) {
  return (
    <button type={type} class={"btn btn-" + variant} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}
