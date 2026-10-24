import { redirect } from "next/navigation";

/** A domain lives in the Data OS Knowledge hub; old links keep their tab and layer. */
export default function DomainPage({ params, searchParams }: { params: { id: string }; searchParams?: Record<string, string | undefined> }) {
  const p = new URLSearchParams();
  Object.entries(searchParams ?? {}).forEach(([k, v]) => { if (v) p.set(k, v); });
  const qs = p.toString();
  redirect(`/os/knowledge/domains/${encodeURIComponent(params.id)}${qs ? `?${qs}` : ""}`);
}
