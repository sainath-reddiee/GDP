import { redirect } from "next/navigation";

/** Jira tickets are on the Tickets page now (with the platform's cases): old /jira links land there. */
export default function JiraPage({ searchParams }: { searchParams?: { tab?: string; key?: string; new?: string } }) {
  const sp = searchParams ?? {};
  const p = new URLSearchParams();
  if (sp.key) { p.set("tab", "ticket"); p.set("key", sp.key); }
  else if (sp.tab === "inbox") p.set("tab", "jira");
  if (sp.new) p.set("new", sp.new);
  redirect(`/os/run/tickets${p.size ? `?${p}` : ""}`);
}
