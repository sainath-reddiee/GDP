import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { KnowledgeItem } from "./actions";
import { KnowledgeBrowser } from "./knowledge-browser";
import { KnowledgeSearch } from "./search-form";

const LIMIT = 50;

export default async function Knowledge({ searchParams }: {
  searchParams?: { q?: string; domain_id?: string; type?: string; status?: string; offset?: string };
}) {
  const sp = searchParams ?? {};
  const offset = Math.max(0, Number(sp.offset ?? 0) || 0);
  const query = new URLSearchParams({ offset: String(offset), limit: String(LIMIT) });
  if (sp.q) query.set("q", sp.q);
  if (sp.domain_id) query.set("domain_id", sp.domain_id);
  if (sp.type) query.set("knowledge_type", sp.type);
  if (sp.status !== "ALL") query.set("status", sp.status || "ACTIVE");
  const [{ domains }, list] = await Promise.all([
    api<{ domains: { domain_id: string; domain_name: string; knowledge_items: number; active_flag: boolean }[] }>("/api/domains"),
    api<{ items: KnowledgeItem[]; total: number }>(`/api/knowledge?${query.toString()}`).catch(() => ({ items: [], total: 0 })),
  ]);
  const active = domains.filter((d) => d.active_flag);
  const total = active.reduce((n, d) => n + d.knowledge_items, 0);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Knowledge"
                  description="Glossary, rules, patterns and approved mappings that ground every mapping, STTM and code generation step." />
      <Card>
        <CardHeader>
          <CardTitle>Ask the knowledge</CardTitle>
          <CardDescription>
            {total} current items across {active.length} domains. Search uses Cortex Search over active items; Ask AI answers
            from the matching items and shows which ones it used. Domain packs live on <Link href="/domains" className="text-primary hover:underline">Domains</Link>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <KnowledgeSearch domains={active.map((d) => d.domain_name)} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Manage knowledge</CardTitle>
          <CardDescription>
            Add, edit (each edit is a new version), retire or restore items. Items locked with a padlock come from a repository
            pack and are re-applied on every deploy; add your own item next to them instead.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <KnowledgeBrowser items={list.items} total={list.total} offset={offset} limit={LIMIT}
                            domains={active.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
        </CardContent>
      </Card>
    </div>
  );
}
