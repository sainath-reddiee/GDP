import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import type { RunState, WhoAmI } from "./types";

export const API_URL = process.env.AIP_API_URL ?? "http://127.0.0.1:8001";
export const AUTH_MODE = process.env.AIP_AUTH ?? "dev";
export const SESSION_COOKIE = "aip_session";
export const DEV_COOKIE = "aip_dev";
export const ROLE_COOKIE = "aip_role";
/** Where an ended session goes: clears the cookies, then shows the login page. */
export const SIGN_OUT = "/bff/signout";

export class ApiError extends Error {
  /** retryAfter: seconds from a Retry-After header (rate limits), when the backend sent one. */
  constructor(public status: number, message: string, public retryAfter: number | null = null) {
    super(message);
  }
}

function sessionHeaders(withRole = true): Record<string, string> {
  const session = cookies().get(SESSION_COOKIE)?.value;
  const role = withRole ? cookies().get(ROLE_COOKIE)?.value : undefined;
  const headers: Record<string, string> = {};
  if (session) headers["X-AIP-Session"] = session;
  if (role) headers["X-AIP-Role"] = role;
  return headers;
}

/** The work role picked earlier is no longer granted (another user signed in on this browser, or it was revoked). */
function staleRole(status: number, text: string): boolean {
  return status === 403 && !!cookies().get(ROLE_COOKIE)?.value && /Role .+ is not available/.test(text);
}

/** Forget the stale work role where cookies can be written (server actions and route handlers); pages just skip it. */
function dropRoleCookie() {
  try { cookies().delete(ROLE_COOKIE); } catch { /* a server component cannot write cookies */ }
}

function detail(text: string): string {
  try {
    const body = JSON.parse(text);
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
  } catch {
    return text;
  }
}

/** Server-side call to the FastAPI backend with the caller's session. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const call = (withRole: boolean) => fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...sessionHeaders(withRole), ...(init.headers ?? {}) },
  });
  let res: Response;
  let text: string;
  try {
    res = await call(true);
    text = await res.text();
    if (staleRole(res.status, text)) {
      dropRoleCookie();
      res = await call(false);
      text = await res.text();
    }
  } catch {
    throw new ApiError(
      503,
      `Cannot reach the API at ${API_URL}. From apps/api run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`,
    );
  }
  if (res.status === 401) redirect(SIGN_OUT);
  if (!res.ok) throw new ApiError(res.status, detail(text), retryAfter(res.headers.get("retry-after")));
  if (res.status === 202 && text.includes("pending_approval")) throw new ApiError(202, detail(text));
  return JSON.parse(text) as T;
}

function retryAfter(header: string | null): number | null {
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds));
  const at = Date.parse(header);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000));
}

/** Multipart POST (file uploads) with the caller's session; the browser sets the boundary header. */
export async function apiForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { method: "POST", body: form, cache: "no-store", headers: sessionHeaders() });
  const text = await res.text();
  if (res.status === 401) redirect(SIGN_OUT);
  if (!res.ok) throw new ApiError(res.status, detail(text));
  if (res.status === 202 && text.includes("pending_approval")) throw new ApiError(202, detail(text));
  return JSON.parse(text) as T;
}

export type ActionResult = { ok: true } | { ok: false; error: string };

/** Server actions return errors instead of throwing so the message reaches the UI. */
export async function attempt(fn: () => Promise<unknown>): Promise<ActionResult> {
  try {
    await fn();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export async function attemptValue<T>(fn: () => Promise<T>): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message };
    throw e;
  }
}

export const getRun = cache((runId: string) => api<RunState>(`/api/runs/${runId}`));

export const whoami = cache(async (): Promise<WhoAmI | null> => {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/auth/me`, { cache: "no-store", headers: sessionHeaders() });
    if (res.status === 403 && cookies().get(ROLE_COOKIE)?.value) {
      res = await fetch(`${API_URL}/api/auth/me`, { cache: "no-store", headers: sessionHeaders(false) });  // a stale work role
    }
  } catch {
    return null;  // API unreachable: pages show their own error
  }
  // the session is over: leave through sign-out so the cookies go too (a page's .catch can never swallow this)
  if (res.status === 401) redirect(SIGN_OUT);
  return res.ok ? ((await res.json()) as WhoAmI) : null;
});

export function sessionHeaderValue(): string | undefined {
  return cookies().get(SESSION_COOKIE)?.value;
}

/** Session and work-role headers for route handlers that proxy a stream to the backend. */
export function proxyHeaders(): Record<string, string> {
  return sessionHeaders();
}

/** Raw response from the backend with the caller's session (binary downloads proxied by route handlers). */
export async function apiRaw(path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, { cache: "no-store", headers: sessionHeaders() });
}
