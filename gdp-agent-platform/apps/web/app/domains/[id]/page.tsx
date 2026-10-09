import Link from "next/link";
import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { displayDomain } from "@/lib/catalog-display";
import type { FeedItem } from "../../knowledge/actions";
import type { DomainMember, DomainRules, DomainVersion, TargetModel } from "../actions";
import type { DomainCard } from "../page";
import { DomainView, type DomainDetail } from "./domain-view";

export default async function DomainPage({ params, searchParams }: { params: { id: string }; searchParams?: { tab?: string } }) {
  let detail: DomainDetail;
  try {
    detail = await api<DomainDetail>(`/api/domains/${params.id}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const id = params.id;
  const [versions, members, rules, columns, feed, cards, list] = await Promise.all([
    api<{ versions: DomainVersion[]; unrecorded_changes: boolean }>(`/api/domains/${id}/versions`).catch(() => ({ versions: [], unrecorded_changes: false })),
    api<{ members: DomainMember[] }>(`/api/domains/${id}/members`).catch(() => ({ members: [] })),
    api<DomainRules>(`/api/domains/${id}/rules`).catch(() => null),
    api<{ targets: TargetModel[] }>(`/api/domains/${id}/columns`).catch(() => ({ targets: [] })),
    api<{ items: FeedItem[] }>(`/api/knowledge/feed?domain_id=${id}&limit=25`).catch(() => ({ items: [] })),
    api<{ cards: Record<string, DomainCard> }>("/api/domain-cards").catch(() => ({ cards: {} as Record<string, DomainCard> })),
    api<{ domains: { domain_id: string; version: number; standard: string | null; active_flag: boolean; knowledge_items: number; target_tables: number }[] }>("/api/domains"),
  ]);
  const row = list.domains.find((d) => d.domain_id === id);
  return (
    <div className="space-y-4">
      <p className="eyebrow">
        <Link href="/domains" className="hover:underline">Domains</Link>
        <span className="mx-1.5 text-muted-foreground">/</span>
        <span className="normal-case tracking-normal text-muted-foreground">{displayDomain(detail.domain.domain_name) ?? detail.domain.domain_name}</span>
      </p>
      <DomainView detail={detail} versions={versions.versions} unrecorded={versions.unrecorded_changes} members={members.members}
                  rules={rules} targets={columns.targets} feed={feed.items} card={cards.cards[id] ?? {}}
                  meta={{ version: row?.version ?? 1, standard: row?.standard ?? null, knowledge: row?.knowledge_items ?? 0,
                          targets: row?.target_tables ?? 0, active: row?.active_flag ?? true }}
                  tab={searchParams?.tab ?? "overview"} />
    </div>
  );
}
