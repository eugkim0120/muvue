type Option = { value: string; label: string };

export function Segmented({ options, value, onChange }: { options: Option[]; value: string; onChange: (v: string) => void }) {
  return (
    <div class="segmented" role="tablist">
      {options.map((o) => (
        <button type="button" role="tab" aria-selected={o.value === value} class={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
