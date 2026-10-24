import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { api, whoami } from "@/lib/api";
import type { WhoAmI } from "@/lib/types";
import { canPreview, canSee, type Grants, type ScreenKey } from "./access";
import { DEFAULT_PERSONA, PERSONAS, PERSONA_COOKIE, personaById, personasFor, type Persona } from "./personas";

// Who is looking at /os and what they are shown. A person is offered the personas a super admin gave them (Admin,
// People; V076); someone from before personas gets the ones their roles match, and a super admin all of them. The
// persona they work as (top bar "View as") is real security: every API call carries it (lib/api, X-AIP-Persona) and
// the API narrows the person's privileges to that persona's role bundle, never above their own. So the grants here
// are the API's narrowed ones, and screens, tabs and actions follow them; `own` keeps the person's full privileges,
// which decide the personas on offer. A super admin works as Super admin (everything) until they pick another one.

export const PREVIEW_COOKIE = "ados_preview_persona";

export type PersonaInfo = { persona_id: string; label: string; description: string | null; roles: string[]; effective: string[]; holders: string[] };
export type OsAccess = Grants & {
  user: string | null;
  /** the signed-in person as the API sees them */
  me: WhoAmI | null;
  /** the signed-in person's own grants, whatever is previewed */
  own: Grants;
  /** the persona a super admin is previewing, or null */
  preview: { id: string; label: string } | null;
  /** privilege -> the role its change requests go to, from the active approval policies */
  approvers: Record<string, string>;
  /** how the personas on offer were found: given by a super admin, matched from roles, or every one (super admin) */
  personaSource: "assigned" | "matched" | "all";
  personas: Persona[];
  persona: Persona;
};

const known = (ids: string[]) => PERSONAS.filter((p) => ids.includes(p.id));

export const osAccess = cache(async (): Promise<OsAccess> => {
  const me = await whoami();
  const own: Grants = { governance: Boolean(me?.governance), privileges: me?.own_privileges ?? me?.privileges ?? [] };
  const working: Grants = { governance: Boolean(me?.governance), privileges: me?.privileges ?? [] };
  const superAdmin = canPreview(own);
  const [mine, policies, catalogue, viewerRole] = await Promise.all([
    own.governance
      ? api<{ personas: string[]; matching: string[]; deployed: boolean }>("/api/me/personas").catch(() => ({ personas: [], matching: [], deployed: false }))
      : Promise.resolve({ personas: [] as string[], matching: [] as string[], deployed: false }),
    own.governance
      ? api<{ policies: { privilege: string; requires_approval: boolean; approver_role: string | null; active: boolean }[] }>("/api/governance/policies")
          .then((r) => r.policies ?? []).catch(() => [])
      : Promise.resolve([]),
    superAdmin ? api<{ personas: PersonaInfo[] }>("/api/governance/personas").then((r) => r.personas ?? []).catch(() => [] as PersonaInfo[])
      : Promise.resolve([] as PersonaInfo[]),
    // a Viewer is anyone without a persona: previewing it uses the VIEWER role's grants
    superAdmin && cookies().get(PREVIEW_COOKIE)?.value === "viewer"
      ? api<{ roles: { role: string; effective: string[] }[] }>("/api/governance/roles").then((r) => r.roles.find((x) => x.role === "VIEWER") ?? null).catch(() => null)
      : Promise.resolve(null),
  ]);

  const wanted = superAdmin ? cookies().get(PREVIEW_COOKIE)?.value ?? null : null;
  const previewed: PersonaInfo | null = wanted === "viewer" && viewerRole
    ? { persona_id: "viewer", label: "Viewer", description: null, roles: ["VIEWER"], effective: viewerRole.effective, holders: [] }
    : wanted ? catalogue.find((p) => p.persona_id === wanted && p.persona_id !== "super") ?? null : null;
  const grants: Grants = previewed ? { governance: true, privileges: previewed.effective } : working;

  let personas: Persona[];
  let personaSource: OsAccess["personaSource"];
  if (previewed) {
    personas = known([previewed.persona_id]);
    personaSource = "assigned";
  } else if (!own.governance || superAdmin) {
    personas = PERSONAS.filter((p) => p.id !== "viewer");
    personaSource = "all";
  } else if (mine.personas.length) {
    personas = known(mine.personas);
    personaSource = "assigned";
  } else {
    // nobody gave this person a persona yet: offer what their roles match (or, before V076, their privileges)
    const matched = known(mine.matching);
    personas = matched.length ? matched : personasFor(own);
    personaSource = "matched";
  }
  if (!personas.length) personas = [personaById("viewer")];
  // the persona the API narrowed to; without a choice a super admin is Super admin, anyone else their first persona
  const chosen = me?.persona ?? cookies().get(PERSONA_COOKIE)?.value ?? (superAdmin ? "super" : DEFAULT_PERSONA);
  return {
    ...grants,
    user: me?.user ?? null,
    me,
    own,
    preview: previewed ? { id: previewed.persona_id, label: previewed.label } : null,
    approvers: Object.fromEntries(policies.filter((p) => p.active && p.requires_approval && p.approver_role).map((p) => [p.privilege, p.approver_role as string])),
    personaSource,
    personas,
    persona: personas.find((p) => p.id === chosen) ?? personas[0],
  };
});

/** Opens an /os screen only for people whose privileges allow it; anyone else goes back to My Work with the reason. */
export async function guard(key: ScreenKey): Promise<OsAccess> {
  const access = await osAccess();
  if (!canSee(access, key)) redirect(`/os?denied=${key}`);
  return access;
}
