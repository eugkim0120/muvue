const PATHS: Record<string, string> = {
  plan: "M4 5h16v4H4zM4 15h7v4H4zM13 15h7v4h-7z",
  inbox: "M3 13l2-8h14l2 8v6H3zM3 13h5l2 3h4l2-3h5",
  activity: "M12 3a9 9 0 1 0 9 9M12 7v5l3 3",
  spend: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  close: "M6 6l12 12M18 6L6 18",
  chevron: "M9 6l6 6-6 6",
  check: "M4 12l5 5L20 6",
  more: "M12 6h.01M12 12h.01M12 18h.01",
};

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return (
    <svg class="icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
