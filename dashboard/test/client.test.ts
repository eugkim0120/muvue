import { api, post, setToken, onForbidden, ApiError } from "../src/api/client";

function mockFetch(status: number, body: unknown, json = true, extra: Record<string, string> = {}) {
  const fn = vi.fn(async () => ({
    ok: status < 400,
    status,
    statusText: "status " + status,
    headers: new Headers({ "content-type": json ? "application/json" : "text/plain", ...extra }),
    json: async () => body,
    text: async () => String(body),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => { vi.unstubAllGlobals(); setToken(""); });

test("post sends JSON with the content type and same-origin credentials", async () => {
  const fetchMock = mockFetch(200, { ok: 1 });
  await post("/x", { a: 1 });
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(init.method).toBe("POST");
  expect(init.body).toBe('{"a":1}');
  expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  expect(init.credentials).toBe("same-origin");
});

test("a token becomes a bearer header", async () => {
  const fetchMock = mockFetch(200, {});
  setToken("abc");
  await api("/x");
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer abc");
});

test("detail is the error message and an auth refusal calls the forbidden hook with its reason", async () => {
  mockFetch(401, { detail: "session expired" }, true, { "x-muvue-auth": "expired" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ message: "session expired", status: 401, auth: "expired" });
  expect(hook).toHaveBeenCalledWith("expired");
});

test("a 403 that is not about sign-in (no X-Muvue-Auth) does not call the forbidden hook", async () => {
  mockFetch(403, { detail: "mutating requests must use Content-Type: application/json" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ status: 403, auth: null });
  expect(hook).not.toHaveBeenCalled();
});

test("a GET auth refusal also reports its reason", async () => {
  mockFetch(403, { detail: "invalid session token" }, true, { "x-muvue-auth": "invalid" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(api("/auth/check")).rejects.toMatchObject({ auth: "invalid" });
  expect(hook).toHaveBeenCalledWith("invalid");
});

test("a bodyless GET sends no Content-Type", async () => {
  const fetchMock = mockFetch(200, {});
  await api("/x");
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
});

test("plain text bodies are returned as text", async () => {
  mockFetch(200, "line1\nline2", false);
  expect(await api<string>("/logs")).toBe("line1\nline2");
});
