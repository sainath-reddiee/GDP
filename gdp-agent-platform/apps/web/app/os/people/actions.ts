"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import { PERSONA_COOKIE } from "@/lib/os/personas";

// Admin, People: a super admin gives people personas and edits what each persona grants (apps/api/app/personas_api.py
// checks ROLE.MANAGE, which only a super admin holds), and previews a persona. The preview cookie is honoured only
// for super admins (lib/os/session).

const PREVIEW_COOKIE = "ados_preview_persona";

export async function assignPersonas(user: string, personas: string[]) {
  const r = await attemptValue(() => api<{ user: string; personas: string[]; roles: string[] }>(
    `/api/governance/people/${encodeURIComponent(user)}/personas`, { method: "PUT", body: JSON.stringify({ personas }) }));
  revalidatePath("/", "layout");
  return r;
}

export async function editPersona(personaId: string, roles: string[]) {
  const r = await attemptValue(() => api(`/api/governance/personas/${encodeURIComponent(personaId)}`, { method: "PUT", body: JSON.stringify({ roles }) }));
  revalidatePath("/", "layout");
  return r;
}

export async function startPreview(personaId: string) {
  cookies().set(PREVIEW_COOKIE, personaId, { path: "/", sameSite: "lax", maxAge: 8 * 3600 });
  cookies().delete(PERSONA_COOKIE);
  revalidatePath("/", "layout");
}
