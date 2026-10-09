import Link from "next/link";
import { ArrowRight, BadgeCheck, BookOpen, Boxes, Clock, Inbox, Play, TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { displayDomain } from "@/lib/catalog-display";
import { ago } from "../skills/types";
import { Avatars, cleanDescription, DomainMark } from "./domain-ui";
import { PackEditor } from "./pack-editor";

type Domain = {
  domain_id: string; domain_name: string; description: string | null; owner: string | null; standard: string | null;
  active_flag: boolean; version: number; knowledge_items: number; target_tables: number;
};
export type DomainCard = {
  members?: { user: string; role: string }[]; runs_30d?: number; inbox?: number; stale?: number; verified?: number;
  last_change?: { version: number; change_kind: string | null; change_note: string | null; created_by: string; created_at: string };
};

export default async function Domains({ searchParams }: { searchParams?: { deleted?: string } }) {
  const showDeleted = searchParams?.deleted === "1";
  const [{ domains }, cards] = await Promise.all([
    api<{ domains: Domain[] }>("/api/domains"),
    api<{ cards: Record<string, DomainCard> }>("/api/domain-cards").catch(() => ({ cards: {} as Record<string, DomainCard> })),
  ]);
  const named = domains.filter((d) => displayDomain(d.domain_name));
  const deletedCount = named.filter((d) => !d.active_flag).length;
  const visible = named.filter((d) => (showDeleted ? !d.active_flag : d.active_flag));

  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Domains"
                  description="Each domain is a product: its contract, target models, rules, knowledge and the people who own it. Every change is a version you can compare and roll back." />
      <PackEditor domains={named.filter((d) => d.active_flag && d.domain_name !== "GDP")
        .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
      {deletedCount > 0 && (
        <div className="flex justify-end text-xs">
          <Link href={showDeleted ? "/domains" : "/domains?deleted=1"} className="text-primary hover:underline">
            {showDeleted ? "Back to active domains" : `Show deleted domains (${deletedCount})`}
          </Link>
        </div>
      )}
      {visible.length === 0 && (
        <p className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">No domains registered yet. Add a knowledge pack above, or deploy the platform to seed the repository packs.</p>
      )}
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {visible.map((d) => {
          const c = cards.cards[d.domain_id] ?? {};
          const name = displayDomain(d.domain_name) ?? d.domain_name;
          const members = c.members ?? [];
          const verifiedPct = d.knowledge_items ? Math.round(((c.verified ?? 0) / d.knowledge_items) * 100) : 0;
          return (
            <Link key={d.domain_id} href={`/domains/${d.domain_id}`}
                  className="group flex flex-col rounded-2xl border bg-card p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
              <div className="flex items-start gap-3">
                <DomainMark name={d.domain_name} />
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-base font-semibold group-hover:text-primary">{name}
                    {d.standard && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">{d.standard}</span>}
                  </p>
                  <p className="line-clamp-2 text-xs text-muted-foreground">{cleanDescription(d.description) ?? "No description yet."}</p>
                </div>
                <ArrowRight className="h-4 w-4 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" />
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                <Stat icon={<Boxes className="h-3.5 w-3.5" />} value={d.target_tables} label="targets" />
                <Stat icon={<BookOpen className="h-3.5 w-3.5" />} value={d.knowledge_items} label="knowledge" />
                <Stat icon={<Play className="h-3.5 w-3.5" />} value={c.runs_30d ?? 0} label="runs, 30 d" />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
                <Chip tone="success" icon={<BadgeCheck className="h-3 w-3" />}>{verifiedPct}% verified</Chip>
                {!!c.inbox && <Chip tone="warning" icon={<Inbox className="h-3 w-3" />}>{c.inbox} to review</Chip>}
                {!!c.stale && <Chip tone="warning" icon={<TriangleAlert className="h-3 w-3" />}>{c.stale} stale</Chip>}
              </div>
              <div className="mt-4 flex items-center gap-2 border-t pt-3 text-[11px] text-muted-foreground">
                {members.length ? <Avatars members={members} /> : <span>No owner yet</span>}
                <span className="ml-auto flex items-center gap-1"><Clock className="h-3 w-3" />
                  v{d.version}{c.last_change ? ` · ${(c.last_change.change_kind ?? "change").toLowerCase()} ${ago(c.last_change.created_at)}` : ""}</span>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ icon, value, label }: { icon: React.ReactNode; value: number; label: string }) {
  return (
    <div className="rounded-xl bg-muted/40 px-2 py-2">
      <p className="flex items-center justify-center gap-1 text-lg font-semibold tabular-nums"><span className="text-muted-foreground">{icon}</span>{value}</p>
      <p className="text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}

function Chip({ tone, icon, children }: { tone: "success" | "warning"; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className={tone === "success" ? "inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
      : "inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"}>{icon}{children}</span>
  );
}
