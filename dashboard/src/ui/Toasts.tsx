import { toasts, dismissToast } from "../state";

export function Toasts() {
  return (
    <div class="toasts" aria-live="polite">
      {toasts.value.map((t) => (
        <button type="button" key={t.id} class={"toast " + t.kind} onClick={() => dismissToast(t.id)}>{t.text}</button>
      ))}
    </div>
  );
}
