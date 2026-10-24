import { redirect } from "next/navigation";

/** Enterprise Memory lives in the Data OS Knowledge hub; old links (?tab=inbox, ?domain_id=...) land on the matching view. */
export default function Knowledge({ searchParams }: { searchParams?: Record<string, string | undefined> }) {
  const p = new URLSearchParams();
  Object.entries(searchParams ?? {}).forEach(([k, v]) => { if (v) p.set(k === "tab" ? "view" : k, v); });
  if (p.get("view") === "overview") p.set("view", "ask");
  const qs = p.toString();
  redirect(`/os/knowledge${qs ? `?${qs}` : ""}`);
}
