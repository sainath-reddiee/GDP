"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { api, attemptValue, ROLE_COOKIE } from "@/lib/api";

export async function loadSnowflakeRoles() {
  return attemptValue(() => api<{ current: string; roles: string[] }>("/api/auth/roles"));
}

export async function setSnowflakeRole(role: string) {
  const picked = role.trim();
  if (!picked) return { ok: false as const, error: "Choose a role" };
  const result = await attemptValue(() =>
    api<{ role: string }>("/api/auth/role", { method: "PUT", body: JSON.stringify({ role: picked }) }),
  );
  if (!result.ok) return result;
  cookies().set(ROLE_COOKIE, result.data.role, { path: "/", sameSite: "lax" });
  revalidatePath("/", "layout");
  return { ok: true as const, role: result.data.role };
}
