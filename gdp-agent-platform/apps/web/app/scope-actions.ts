"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { DOMAIN_COOKIE } from "@/lib/domain-scope";

/** Sets the top-bar domain scope ("" = all domains) and re-renders every page with it. */
export async function setDomainScope(domain: string) {
  const name = domain.trim().slice(0, 128);
  if (name) cookies().set(DOMAIN_COOKIE, name, { path: "/", sameSite: "lax", maxAge: 60 * 60 * 24 * 365 });
  else cookies().delete(DOMAIN_COOKIE);
  revalidatePath("/", "layout");
  return { ok: true as const, domain: name };
}
