/** The header's domain scope was retired: pages show every domain the person may see, and narrow by ?domain= alone.
 *  Kept so the classic stores read one place; a leftover aip_domain cookie no longer filters anything. */
export const DOMAIN_COOKIE = "aip_domain";

export function getDomainScope(): string {
  return "";
}

/** A page's explicit ?domain= ("all" clears it for that view). */
export function scopedDomain(param?: string | null): string {
  if (param === "all") return "";
  return param ?? getDomainScope();
}
