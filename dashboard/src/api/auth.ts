import { routes } from "./routes";
import { api, ApiError, setToken } from "./client";

export async function checkAuth(): Promise<boolean> {
  try {
    await api(routes.authCheck());
    return true;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return false;
    throw e;
  }
}

// `muvue serve` prints a link whose `#n=` fragment is a single-use
// nonce. It is exchanged once for an HttpOnly cookie; in an iframe the
// exchange also returns the token for the Authorization header.
export async function exchangeFragmentNonce(): Promise<boolean> {
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
  }
  // A used or expired nonce is not an error: the page is read-only.
  return checkAuth();
}
