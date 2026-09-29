import { useState } from "preact/hooks";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import { signInWithToken, applyAuthResult, type AuthResult } from "../api/auth";
import { signInOpen, refresh, toast } from "../state";

const REFUSED: Record<Exclude<AuthResult, "ok">, string> = {
  missing: "Paste the api token first.",
  invalid: "That token was not accepted. Tokens change every time muvue serve restarts — run muvue link for the current one.",
  expired: "That token's session expired after 8 hours without activity. Run muvue link on the server to start a new one.",
};

export function SignInSheet() {
  const [value, setValue] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const a = useAction();
  const close = () => { signInOpen.value = false; };

  async function submit(e: Event) {
    e.preventDefault();
    setRefused(null);
    // `as`, not an annotation: an annotated `let` narrows to "missing" here,
    // and TS can't see the closure's assignment, so the checks below would
    // fail to compile.
    let result = "missing" as AuthResult;
    const ok = await a.run(async () => { result = await signInWithToken(value); });
    setValue("");
    if (!ok) return;
    if (result === "ok") {
      applyAuthResult("ok");
      toast("Signed in");
      close();
      refresh();
      return;
    }
    setRefused(REFUSED[result]);
    if (result === "expired") applyAuthResult("expired");
  }

  return (
    <Sheet title="Sign in" onClose={close}>
      <div class="stack">
        <p>Signing in lets this page approve, plan and run. Either way works:</p>
        <div class="stack tight">
          <h3>Open a fresh link</h3>
          <p class="muted">On the machine running muvue, in the project's folder, run <code>muvue link</code>, then open the link it prints on this device.</p>
        </div>
        <form class="stack tight" onSubmit={submit}>
          <h3>Or paste the api token</h3>
          <p class="muted"><code>muvue serve</code> and <code>muvue link</code> print it. It changes every time <code>muvue serve</code> restarts.</p>
          <div class="row nowrap">
            <input type="password" autocomplete="off" aria-label="api token" placeholder="api token" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
            <Button type="submit" variant="filled" busy={a.busy} busyLabel="Checking…" disabled={!value.trim()}>Use token</Button>
          </div>
          {refused ? <div class="callout danger" data-sign-in-error>{refused}</div> : null}
          {a.error ? <div class="callout danger">{a.error}</div> : null}
        </form>
      </div>
    </Sheet>
  );
}
