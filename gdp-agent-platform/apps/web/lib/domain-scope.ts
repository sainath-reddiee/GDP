import { cookies } from "next/headers";

/** The domain picked in the top bar, by name ("" = all domains). Every domain-aware API accepts a name or an id. */
export const DOMAIN_COOKIE = "aip_domain";

export function getDomainScope(): string {
  return cookies().get(DOMAIN_COOKIE)?.value ?? "";
}

/** A page's explicit ?domain= wins over the top-bar scope; "all" in the URL clears it for that view. */
export function scopedDomain(param?: string | null): string {
  if (param === "all") return "";
  return param ?? getDomainScope();
}
