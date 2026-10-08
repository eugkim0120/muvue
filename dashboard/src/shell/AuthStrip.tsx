import { authed, signedOutReason, signInOpen, tailnetLogin } from "../state";
import { Button } from "../ui/Button";

const COPY = {
  read_only: { text: "Read-only view.", action: "Sign in" },
  stale: { text: "Signed out: muvue serve restarted or muvue link issued a new token.", action: "Sign in" },
  expired: { text: "Signed out after sitting idle too long.", action: "Sign in again" },
} as const;

// One line, not a form: the form lives in SignInSheet, one tap away.
export function AuthStrip() {
  if (authed.value) {
    const login = tailnetLogin.value;
    if (login === null) return null;
    return <div class="auth-strip" data-auth-strip="tailnet" role="status"><span class="grow">Signed in via Tailscale ({login}).</span></div>;
  }
  const reason = signedOutReason.value;
  const c = COPY[reason];
  return (
    <div class={"auth-strip" + (reason === "read_only" ? "" : " warn")} data-auth-strip={reason} role="status">
      <span class="grow">{c.text}</span>
      <Button variant="outline" onClick={() => { signInOpen.value = true; }}>{c.action}</Button>
    </div>
  );
}
