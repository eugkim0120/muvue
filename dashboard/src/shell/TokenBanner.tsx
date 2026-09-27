import { useState } from "preact/hooks";
import { setToken } from "../api/client";
import { checkAuth } from "../api/auth";
import { setAuthed, toastError } from "../state";
import { Button } from "../ui/Button";

export function TokenBanner() {
  const [value, setValue] = useState("");
  async function use() {
    setToken(value.trim());
    setValue("");
    try { setAuthed(await checkAuth()); } catch (e) { toastError(e); }
  }
  return (
    <form class="token-banner" onSubmit={(e) => { e.preventDefault(); void use(); }}>
      <div class="caption">Read-only. Paste the api token printed by <code>muvue serve</code> to act. It stays in this tab's memory only.</div>
      <div class="row">
        <input type="password" placeholder="api token printed by muvue serve" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
        <Button type="submit" variant="filled">Use token</Button>
      </div>
    </form>
  );
}
