"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { api, attempt, DEV_COOKIE, SESSION_COOKIE, type ActionResult } from "@/lib/api";

export async function login(_: ActionResult | null, form: FormData): Promise<ActionResult> {
  let sessionId = "";
  const result = await attempt(async () => {
    const res = await api<{ session_id: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ user: form.get("user"), token: form.get("token") }),
    });
    sessionId = res.session_id;
  });
  if (!result.ok) return result;
  cookies().set(SESSION_COOKIE, sessionId, { ...cookieOptions, maxAge: 8 * 3600 });
  redirect("/dashboard");
}

const cookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/",
};

export async function continueDev() {
  cookies().set(DEV_COOKIE, "1", { ...cookieOptions, maxAge: 8 * 3600 });
  redirect("/dashboard");
}

export async function logout() {
  await attempt(() => api("/api/auth/logout", { method: "POST" }));
  cookies().delete(SESSION_COOKIE);
  cookies().delete(DEV_COOKIE);
  redirect("/login");
}
