import { routes } from "./routes";
import { api, post, ApiError, setToken, authProblemOf, type AuthProblem } from "./client";
import { setSignedIn, setSignedOut } from "../state";

export type AuthResult = "ok" | AuthProblem;

export async function checkAuth(): Promise<AuthResult> {
  try {
    await api(routes.authCheck());
    return "ok";
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return e.auth ?? "missing";
    throw e;
  }
}

// `muvue serve` and `muvue link` print a link whose `#n=` fragment is a
// single-use nonce. It is exchanged once for an HttpOnly cookie; in an
// iframe the exchange also returns the token for the Authorization header.
export async function exchangeFragmentNonce(): Promise<AuthResult> {
  const match = (window.location.hash || "").match(/(?:^#|[&#])n=([^&]+)/);
  if (!match) return checkAuth();
  const embedded = window.top !== window.self;
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const r = await fetch(routes.authExchange(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ nonce: decodeURIComponent(match[1]!), header: embedded }),
  });
  if (r.ok) {
    const body = (await r.json()) as { token?: string };
    if (body.token) setToken(body.token);
  } else if (authProblemOf(r.status, r.headers.get("x-muvue-auth")) === "expired") {
    return "expired";
  }
  // An already-used nonce is not an error: a cookie from an earlier exchange may still be live.
  return checkAuth();
}

// A pasted api token: check it, then ask the daemon to set the HttpOnly
// cookie so a reload stays signed in. A refused token is forgotten at once.
export async function signInWithToken(pasted: string): Promise<AuthResult> {
  const t = pasted.trim();
  if (!t) return "missing";
  setToken(t);
  const result = await checkAuth();
  if (result !== "ok") {
    setToken("");
    return result;
  }
  await post(routes.authSession());
  return "ok";
}

export function applyAuthResult(r: AuthResult): void {
  if (r === "ok") setSignedIn();
  else setSignedOut(r === "expired" ? "expired" : r === "invalid" ? "stale" : "read_only");
}
