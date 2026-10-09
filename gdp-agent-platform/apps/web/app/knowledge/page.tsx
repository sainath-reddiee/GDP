import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";
import type { FeedItem, InboxItem, KnowledgeItem, Overview } from "./actions";
import { KnowledgeBrowser } from "./knowledge-browser";
import { HUB_TABS, type HubTab } from "./hub-tabs";
import { FeedPanel, HealthPanel, HubShell, InboxPanel, OverviewPanel } from "./knowledge-hub";
import { KnowledgeSearch } from "./search-form";

const LIMIT = 50;
type Pair = { a_id: string; a_title: string; a_origin: string; b_id: string; b_title: string; b_origin: string; knowledge_type: string; domain_name: string; score: number };

export default async function Knowledge({ searchParams }: {
  searchParams?: { q?: string; domain_id?: string; type?: string; status?: string; offset?: string; tab?: string; origin?: string };
}) {
  const sp = searchParams ?? {};
  const tab = (HUB_TABS.find((t) => t.id === sp.tab)?.id ?? "overview") as HubTab;
  const scope = sp.domain_id ? `?domain_id=${encodeURIComponent(sp.domain_id)}` : "";
  const [{ domains }, overview] = await Promise.all([
    api<{ domains: { domain_id: string; domain_name: string; knowledge_items: number; active_flag: boolean }[] }>("/api/domains"),
    api<Overview>(`/api/knowledge/overview${scope}`).catch(() => null),
  ]);
  const active = domains.filter((d) => d.active_flag);
  const domainList = active.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }));

  let body: React.ReactNode = null;
  if (!overview && tab !== "items") {
    body = <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">The knowledge hub needs the latest deploy (migration V022). All items still works.</p>;
  } else if (tab === "overview" && overview) {
    body = (
      <OverviewPanel overview={overview} ask={
        <section className="rounded-2xl border bg-card p-4 shadow-sm">
          <h3 className="text-sm font-semibold">Ask the knowledge</h3>
          <p className="mb-3 text-xs text-muted-foreground">Search uses Cortex Search over items in use; Ask AI answers from the matching items and cites them.
            Domain packs live on <Link href="/domains" className="text-primary hover:underline">Domains</Link>.</p>
          <KnowledgeSearch domains={active.map((d) => d.domain_name)} />
        </section>
      } />
    );
  } else if (tab === "items") {
    const offset = Math.max(0, Number(sp.offset ?? 0) || 0);
    const query = new URLSearchParams({ offset: String(offset), limit: String(LIMIT) });
    if (sp.q) query.set("q", sp.q);
    if (sp.domain_id) query.set("domain_id", sp.domain_id);
    if (sp.type) query.set("knowledge_type", sp.type);
    if (sp.status !== "ALL") query.set("status", sp.status || "ACTIVE");
    const list = await api<{ items: KnowledgeItem[]; total: number }>(`/api/knowledge?${query.toString()}`).catch(() => ({ items: [], total: 0 }));
    body = <KnowledgeBrowser items={list.items} total={list.total} offset={offset} limit={LIMIT} domains={domainList} />;
  } else if (tab === "inbox") {
    const inbox = await api<{ items: InboxItem[] }>(`/api/knowledge/inbox${scope}`).catch(() => ({ items: [] }));
    body = <InboxPanel items={inbox.items} />;
  } else if (tab === "feed") {
    const q = new URLSearchParams({ limit: "150" });
    if (sp.domain_id) q.set("domain_id", sp.domain_id);
    if (sp.origin) q.set("origin", sp.origin);
    const feed = await api<{ items: FeedItem[] }>(`/api/knowledge/feed?${q}`).catch(() => ({ items: [] }));
    body = <FeedPanel items={feed.items} />;
  } else if (tab === "health") {
    const [stale, dupes] = await Promise.all([
      api<{ items: KnowledgeItem[]; stale_days: number }>(`/api/knowledge/stale${scope}`).catch(() => ({ items: [], stale_days: 180 })),
      api<{ pairs: Pair[] }>(`/api/knowledge/duplicates${scope}`).catch(() => ({ pairs: [] })),
    ]);
    body = <HealthPanel stale={stale.items} staleDays={stale.stale_days} pairs={dupes.pairs} />;
  }

  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Knowledge hub"
                  description="Everything the agents know: glossary, rules, patterns, approved mappings and models. Every approval and decision teaches it; every change is a version you can compare and roll back." />
      <HubShell tab={tab} overview={overview}>{body}</HubShell>
    </div>
  );
}
