import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { api, whoami } from "@/lib/api";
import type { WhoAmI } from "@/lib/types";
import { canPreview, canSee, type Grants, type ScreenKey } from "./access";
import { DEFAULT_PERSONA, PERSONA_COOKIE, personaById, personasFor, type Persona } from "./personas";

// Who is looking at /os and what they are shown. The grants are the signed-in person's governance privileges, or,
// when a super or governance admin previews a role, that role's effective privileges. A preview only changes what
// the UI shows: the API still answers as the signed-in person.

export const PREVIEW_COOKIE = "ados_preview_role";

export type RoleInfo = { role: string; description: string; effective: string[]; members: unknown[] };
export type OsAccess = Grants & {
  user: string | null;
  /** the signed-in person as the API sees them */
  me: WhoAmI | null;
  /** the signed-in person's own grants, whatever is previewed */
  own: Grants;
  /** the role being previewed, or null */
  preview: string | null;
  /** the roles an admin may preview (empty for everyone else) */
  roles: { role: string; description: string; members: number }[];
  /** privilege -> the role its change requests go to, from the active approval policies */
  approvers: Record<string, string>;
  personas: Persona[];
  persona: Persona;
};

export const osAccess = cache(async (): Promise<OsAccess> => {
  const me = await whoami();
  const own: Grants = { governance: Boolean(me?.governance), privileges: me?.privileges ?? [] };
  const admin = canPreview(own);
  const [roles, policies] = await Promise.all([
    admin ? api<{ roles: RoleInfo[] }>("/api/governance/roles").then((r) => r.roles ?? []).catch(() => [] as RoleInfo[]) : Promise.resolve([] as RoleInfo[]),
    own.governance
      ? api<{ policies: { privilege: string; requires_approval: boolean; approver_role: string | null; active: boolean }[] }>("/api/governance/policies")
          .then((r) => r.policies ?? []).catch(() => [])
      : Promise.resolve([]),
  ]);
  const wanted = admin ? cookies().get(PREVIEW_COOKIE)?.value ?? null : null;
  const previewed = wanted ? roles.find((r) => r.role === wanted) ?? null : null;
  const grants: Grants = previewed ? { governance: true, privileges: previewed.effective } : own;
  const personas = personasFor(grants);
  const chosen = cookies().get(PERSONA_COOKIE)?.value ?? DEFAULT_PERSONA;
  return {
    ...grants,
    user: me?.user ?? null,
    me,
    own,
    preview: previewed?.role ?? null,
    roles: roles.map((r) => ({ role: r.role, description: r.description, members: Array.isArray(r.members) ? r.members.length : 0 })),
    approvers: Object.fromEntries(policies.filter((p) => p.active && p.requires_approval && p.approver_role).map((p) => [p.privilege, p.approver_role as string])),
    personas,
    persona: personas.find((p) => p.id === chosen) ?? personas[0] ?? personaById("viewer"),
  };
});

/** Opens an /os screen only for people whose privileges allow it; anyone else goes back to My Work with the reason. */
export async function guard(key: ScreenKey): Promise<OsAccess> {
  const access = await osAccess();
  if (!canSee(access, key)) redirect(`/os?denied=${key}`);
  return access;
}
