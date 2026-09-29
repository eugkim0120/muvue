// Progressive disclosure: one line says what matters (nothing real will be
// written); the how-to-fix stays one tap away.
export function FakeAgentNotice() {
  return (
    <details class="demo-notice" data-fake-notice>
      <summary>Demo agent: writes no code</summary>
      <p class="muted">Tasks here are routed to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</p>
    </details>
  );
}
