export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Inside the VS Code webview the session cookie is never sent, so the
// nonce exchange hands back the token and it is kept here, in memory.
let token = "";
let forbidden: () => void = () => {};

export function setToken(t: string): void { token = t; }
export function onForbidden(handler: () => void): void { forbidden = handler; }

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) };
  if (token) headers["Authorization"] = "Bearer " + token;
  const r = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const type = r.headers.get("content-type") || "";
  const body: unknown = type.startsWith("application/json") ? await r.json() : await r.text();
  if (r.status === 403 && init.method === "POST") forbidden();
  if (!r.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body ? String((body as { detail: unknown }).detail) : "";
    throw new ApiError(detail || r.statusText || `HTTP ${r.status}`, r.status);
  }
  return body as T;
}

export function post<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
