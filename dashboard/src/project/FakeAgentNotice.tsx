import { useState } from "preact/hooks";

const KEY = "muvue.demoNoticeDismissed";

function wasDismissed(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

// Per-viewer convenience only; a blocked storage just means the banner returns.
export function FakeAgentNotice() {
  const [hidden, setHidden] = useState(wasDismissed);
  const [open, setOpen] = useState(false);
  if (hidden) return null;
  function dismiss() {
    try { localStorage.setItem(KEY, "1"); } catch { /* storage blocked: the banner will show again next visit */ }
    setHidden(true);
  }
  return (
    <div class="demo-banner" data-fake-notice role="note">
      <p>Demo agent: writes no code</p>
      <button type="button" class="demo-details" aria-expanded={open} onClick={() => setOpen(!open)}>Details</button>
      <button type="button" class="icon-btn" aria-label="dismiss demo notice" onClick={dismiss}>✕</button>
      {open ? <p class="demo-more">Tasks go to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</p> : null}
    </div>
  );
}
