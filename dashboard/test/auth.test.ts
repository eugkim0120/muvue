import { checkAuth, signInWithToken, applyAuthResult, handleLinkHashChange, loadWhoami } from "../src/api/auth";
import { setToken } from "../src/api/client";
import { authed, signedOutReason, refreshTick, tailnetLogin } from "../src/state";

type Reply = { status: number; auth?: string };
function stub(replies: Record<string, Reply>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const r = replies[url] ?? { status: 200 };
    return {
      ok: r.status < 400, status: r.status, statusText: "s",
      headers: new Headers({ "content-type": "application/json", ...(r.auth ? { "x-muvue-auth": r.auth } : {}) }),
      json: async () => ({ detail: "d" }), text: async () => "",
      _init: init,
    };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => { vi.unstubAllGlobals(); setToken(""); authed.value = false; signedOutReason.value = "read_only"; tailnetLogin.value = null; });

test("checkAuth maps 200/403 missing/403 invalid/401 expired", async () => {
  stub({ "/auth/check": { status: 200 } });
  expect(await checkAuth()).toBe("ok");
  stub({ "/auth/check": { status: 403, auth: "missing" } });
  expect(await checkAuth()).toBe("missing");
  stub({ "/auth/check": { status: 403, auth: "invalid" } });
  expect(await checkAuth()).toBe("invalid");
  stub({ "/auth/check": { status: 401, auth: "expired" } });
  expect(await checkAuth()).toBe("expired");
});

test("signInWithToken: a good token is checked, then turned into the session cookie", async () => {
  const fetchMock = stub({ "/auth/check": { status: 200 }, "/auth/session": { status: 200 } });
  expect(await signInWithToken("  tok  ")).toBe("ok");
  const urls = fetchMock.mock.calls.map((c) => c[0]);
  expect(urls).toEqual(["/auth/check", "/auth/session"]);
  const checkInit = fetchMock.mock.calls[0]![1] as RequestInit;
  expect((checkInit.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok");
});

test("signInWithToken: a refused token is forgotten and reported, and no cookie is requested", async () => {
  const fetchMock = stub({ "/auth/check": { status: 403, auth: "invalid" } });
  expect(await signInWithToken("wrong")).toBe("invalid");
  expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/auth/check"]);
  const again = stub({ "/auth/check": { status: 200 } });
  await checkAuth();
  expect(((again.mock.calls[0]![1] as RequestInit).headers as Record<string, string>)["Authorization"]).toBeUndefined();
});

test("signInWithToken: blank input never calls the server", async () => {
  const fetchMock = stub({});
  expect(await signInWithToken("   ")).toBe("missing");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("applyAuthResult sets authed and the signed-out reason", () => {
  applyAuthResult("ok");
  expect(authed.value).toBe(true);
  applyAuthResult("expired");
  expect([authed.value, signedOutReason.value]).toEqual([false, "expired"]);
  applyAuthResult("invalid");
  expect(signedOutReason.value).toBe("stale");
  applyAuthResult("missing");
  expect(signedOutReason.value).toBe("read_only");
});

test("signInWithToken: if the cookie request fails the client token is forgotten and the error propagates", async () => {
  stub({ "/auth/check": { status: 200 }, "/auth/session": { status: 500 } });
  await expect(signInWithToken("tok")).rejects.toThrow();
  const again = stub({ "/auth/check": { status: 200 } });
  await checkAuth();
  expect(((again.mock.calls[0]![1] as RequestInit).headers as Record<string, string>)["Authorization"]).toBeUndefined();
});

test("a muvue link pasted into an open tab is exchanged, scrubbed and signs the page in", async () => {
  window.location.hash = "#n=abc123";
  const fetchMock = stub({ "/auth/exchange": { status: 200 }, "/auth/check": { status: 200 } });
  const tick = refreshTick.value;
  await handleLinkHashChange();
  expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/auth/exchange", "/auth/check"]);
  expect(JSON.parse((fetchMock.mock.calls[0]![1] as RequestInit).body as string).nonce).toBe("abc123");
  expect(window.location.hash).toBe("");
  expect(authed.value).toBe(true);
  expect(refreshTick.value).toBe(tick + 1);
});

test("a hash change that is not a link does nothing", async () => {
  window.location.hash = "#/p/1";
  const fetchMock = stub({});
  await handleLinkHashChange();
  expect(fetchMock).not.toHaveBeenCalled();
  window.location.hash = "";
});

test("loadWhoami records the tailnet login, or null for a token session", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200, statusText: "OK", headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ login: "me@example.com" }), text: async () => "",
  })));
  await loadWhoami();
  expect(tailnetLogin.value).toBe("me@example.com");
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200, statusText: "OK", headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ login: null }), text: async () => "",
  })));
  await loadWhoami();
  expect(tailnetLogin.value).toBeNull();
});
