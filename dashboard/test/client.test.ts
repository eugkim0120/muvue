import { api, post, setToken, onForbidden, ApiError } from "../src/api/client";

function mockFetch(status: number, body: unknown, json = true) {
  const fn = vi.fn(async () => ({
    ok: status < 400,
    status,
    statusText: "status " + status,
    headers: new Headers({ "content-type": json ? "application/json" : "text/plain" }),
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

test("detail is the error message and 403 on POST calls the forbidden hook", async () => {
  mockFetch(403, { detail: "no session" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ message: "no session", status: 403 });
  expect(hook).toHaveBeenCalledTimes(1);
});

test("403 on GET does not call the forbidden hook", async () => {
  mockFetch(403, { detail: "no" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
  expect(hook).not.toHaveBeenCalled();
});

test("plain text bodies are returned as text", async () => {
  mockFetch(200, "line1\nline2", false);
  expect(await api<string>("/logs")).toBe("line1\nline2");
});
