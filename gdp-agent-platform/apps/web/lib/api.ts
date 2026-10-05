import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import type { RunState, WhoAmI } from "./types";

export const API_URL = process.env.AIP_API_URL ?? "http://127.0.0.1:8001";
export const AUTH_MODE = process.env.AIP_AUTH ?? "dev";
export const SESSION_COOKIE = "aip_session";
export const DEV_COOKIE = "aip_dev";
export const ROLE_COOKIE = "aip_role";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function sessionHeaders(): Record<string, string> {
  const session = cookies().get(SESSION_COOKIE)?.value;
  const role = cookies().get(ROLE_COOKIE)?.value;
  const headers: Record<string, string> = {};
  if (session) headers["X-AIP-Session"] = session;
  if (role) headers["X-AIP-Role"] = role;
  return headers;
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
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...sessionHeaders(), ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      503,
      `Cannot reach the API at ${API_URL}. From apps/api run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`,
    );
  }
  const text = await res.text();
  if (res.status === 401) redirect("/login");
  if (!res.ok) throw new ApiError(res.status, detail(text));
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
  try {
    const res = await fetch(`${API_URL}/api/auth/me`, { cache: "no-store", headers: sessionHeaders() });
    return res.ok ? ((await res.json()) as WhoAmI) : null;
  } catch {
    return null;
  }
});

export function sessionHeaderValue(): string | undefined {
  return cookies().get(SESSION_COOKIE)?.value;
}
