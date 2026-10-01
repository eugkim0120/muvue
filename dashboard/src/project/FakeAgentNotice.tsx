import { useState } from "preact/hooks";

const KEY = "muvue.demoNoticeDismissed";

function wasDismissed(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

// Per-viewer convenience only; a blocked storage means the banner returns on reload, and the viewer is told so.
export function FakeAgentNotice() {
  const [hidden, setHidden] = useState(wasDismissed);
  const [open, setOpen] = useState(false);
  const [notSaved, setNotSaved] = useState(false);
  if (hidden) return notSaved ? <p class="caption" data-notice-not-saved>Couldn't remember this; it will return on reload.</p> : null;
  function dismiss() {
    try { localStorage.setItem(KEY, "1"); } catch { setNotSaved(true); }
    setHidden(true);
  }
  return (
    <div class="demo-banner" data-fake-notice role="note">
      <p>Demo agent: writes no code</p>
      <button type="button" class="demo-details" aria-expanded={open} aria-controls="demo-notice-more" onClick={() => setOpen(!open)}>Details</button>
      <button type="button" class="icon-btn" aria-label="dismiss demo notice" onClick={dismiss}>✕</button>
      <p class="demo-more" id="demo-notice-more" hidden={!open}>Tasks go to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</p>
    </div>
  );
}
