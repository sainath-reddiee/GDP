import { redirect } from "next/navigation";

/** Sources moved into the Source Hub; old links (with ?db=&schema=&mode=) keep working. */
export default function SourcesRedirect({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(searchParams ?? {})) {
    if (typeof v === "string") qs.set(k, v);
  }
  const query = qs.toString();
  redirect(`/dashboard/sources${query ? `?${query}` : ""}`);
}
