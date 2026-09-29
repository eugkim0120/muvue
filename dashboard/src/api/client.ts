export type AuthProblem = "missing" | "invalid" | "expired";

export class ApiError extends Error {
  status: number;
  auth: AuthProblem | null;
  constructor(message: string, status: number, auth: AuthProblem | null = null) {
    super(message);
    this.status = status;
    this.auth = auth;
  }
}

// Inside the VS Code webview the session cookie is never sent, so the
// nonce exchange hands back the token and it is kept here, in memory. A
// pasted token also lives here until /auth/session turns it into the cookie.
let token = "";
let forbidden: (problem: AuthProblem) => void = () => {};

export function setToken(t: string): void { token = t; }
export function onForbidden(handler: (problem: AuthProblem) => void): void { forbidden = handler; }

// Only responses the daemon marks with X-Muvue-Auth are about sign-in; a
// 403 for a wrong Content-Type or Origin is not.
export function authProblemOf(status: number, header: string | null): AuthProblem | null {
  if (status !== 401 && status !== 403) return null;
  return header === "missing" || header === "invalid" || header === "expired" ? header : null;
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const sendsBody = init.body !== undefined || (init.method !== undefined && init.method !== "GET");
  const headers: Record<string, string> = { ...(sendsBody ? { "Content-Type": "application/json" } : {}), ...(init.headers as Record<string, string> | undefined) };
  if (token) headers["Authorization"] = "Bearer " + token;
  const r = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const type = r.headers.get("content-type") || "";
  const body: unknown = type.startsWith("application/json") ? await r.json() : await r.text();
  const problem = authProblemOf(r.status, r.headers.get("x-muvue-auth"));
  if (problem) forbidden(problem);
  if (!r.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body ? String((body as { detail: unknown }).detail) : "";
    throw new ApiError(detail || r.statusText || `HTTP ${r.status}`, r.status, problem);
  }
  return body as T;
}

export function post<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
