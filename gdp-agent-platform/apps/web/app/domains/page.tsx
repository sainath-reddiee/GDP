import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { displayDomain } from "@/lib/catalog-display";
import Link from "next/link";
import { DomainTools } from "./domain-tools";
import { PackEditor } from "./pack-editor";

type Domain = {
  domain_id: string; domain_name: string; description: string | null; owner: string | null;
  active_flag: boolean; version: number; knowledge_items: number; target_tables: number;
};

type DomainTarget = {
  target_table_id: string; target_database: string; target_schema: string; target_table: string;
  description: string | null; column_count: number; role: "hub" | "spoke" | null; hub_fk: string | null;
  hkey_columns: string[]; minimum_mapping: string[]; lookups: string[]; casts: number;
};

type DomainDetail = {
  targets: DomainTarget[];
  signals: { tables?: Record<string, number>; columns?: Record<string, number> };
  source_systems: { skey: number; name: string; bronze_schema: string }[];
  contract: string | null;
  silver: { database: string | null; schema: string | null };
  knowledge: { knowledge_type: string; n: number }[];
  deletable?: boolean;
  not_deletable_reason?: string | null;
  active_runs?: { run_id: string; run_name: string; current_state: string }[];
  deleted?: { at: string; by: string } | null;
};

function topSignals(weights?: Record<string, number>, limit = 10) {
  return Object.entries(weights ?? {}).sort((a, b) => b[1] - a[1]).slice(0, limit).map(([k]) => k);
}

function cleanDescription(text: string | null) {
  return (text || "").replace(/Global Data Platform\s*/i, "").replace(/\bGDP\b/g, "").trim() || null;
}

export default async function Domains({ searchParams }: { searchParams?: { deleted?: string } }) {
  const showDeleted = searchParams?.deleted === "1";
  const { domains } = await api<{ domains: Domain[] }>("/api/domains");
  const named = domains.filter((d) => displayDomain(d.domain_name));
  const deletedCount = named.filter((d) => !d.active_flag).length;
  const visible = named.filter((d) => (showDeleted ? !d.active_flag : d.active_flag));
  const details = await Promise.all(visible.map((d) =>
    api<DomainDetail>(`/api/domains/${d.domain_id}`).catch(() => null)));

  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Knowledge" title="Domains"
                  description="Domain contracts drive source detection on the Sources page and the mapping, STTM and dbt generation for every run." />
      {deletedCount > 0 && (
        <div className="flex justify-end text-xs">
          <Link href={showDeleted ? "/domains" : "/domains?deleted=1"} className="text-primary hover:underline">
            {showDeleted ? "Back to active domains" : `Show deleted domains (${deletedCount})`}
          </Link>
        </div>
      )}
      <PackEditor domains={named.filter((d) => d.active_flag && d.domain_name !== "GDP")
        .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
      {visible.length === 0 && (
        <Card className="p-5 text-sm text-muted-foreground">No domains registered yet. Add a knowledge pack above, or deploy the platform to seed the repository packs.</Card>
      )}
      {visible.map((d, i) => {
        const detail = details[i];
        const tableSignals = topSignals(detail?.signals.tables, 8);
        const columnSignals = topSignals(detail?.signals.columns, 12);
        return (
          <Card key={d.domain_id} className="space-y-4 p-5">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold">{displayDomain(d.domain_name)}</h3>
                <p className="text-sm text-muted-foreground">{cleanDescription(d.description) ?? "No description"}</p>
                {detail?.silver.database && (
                  <p className="mt-1 font-mono text-[11px] text-muted-foreground">{detail.silver.database}.{detail.silver.schema}</p>
                )}
              </div>
              <div className="flex gap-4 text-right text-sm">
                <div><div className="text-lg font-semibold tabular-nums">{d.target_tables}</div><div className="text-xs text-muted-foreground">targets</div></div>
                <div><div className="text-lg font-semibold tabular-nums">{d.knowledge_items}</div><div className="text-xs text-muted-foreground">knowledge items</div></div>
                <div><div className="text-lg font-semibold tabular-nums">{detail?.source_systems.length ?? 0}</div><div className="text-xs text-muted-foreground">source systems</div></div>
              </div>
            </div>

            {detail && detail.targets.length > 0 && (
              <Table>
                <THead>
                  <TR><TH>Target</TH><TH>Role</TH><TH className="text-right">Columns</TH><TH>HKEY</TH><TH>Reference lookups</TH><TH className="text-right">Casts</TH></TR>
                </THead>
                <TBody>
                  {detail.targets.map((t) => (
                    <TR key={t.target_table_id}>
                      <TD>
                        <div className="font-medium">{t.target_table}</div>
                        {t.hub_fk && <div className="text-[11px] text-muted-foreground">joins hub on {t.hub_fk}</div>}
                      </TD>
                      <TD>{t.role ? <Badge variant={t.role === "hub" ? "default" : "outline"}>{t.role}</Badge> : "—"}</TD>
                      <TD className="text-right tabular-nums">{t.column_count}</TD>
                      <TD className="max-w-[260px] text-xs text-muted-foreground">{t.hkey_columns.join(", ") || "—"}</TD>
                      <TD className="max-w-[260px] text-xs text-muted-foreground">{t.lookups.join(", ") || "—"}</TD>
                      <TD className="text-right tabular-nums">{t.casts || "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}

            {detail && (
              <div className="grid gap-4 md:grid-cols-3">
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Detection signals</p>
                  {tableSignals.length + columnSignals.length === 0 ? <p className="text-sm text-muted-foreground">None configured</p> : (
                    <div className="flex flex-wrap gap-1">
                      {tableSignals.map((s) => <Badge key={`t-${s}`} variant="secondary">{s}</Badge>)}
                      {columnSignals.map((s) => <Badge key={`c-${s}`} variant="outline">{s}</Badge>)}
                    </div>
                  )}
                </div>
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Source systems</p>
                  {detail.source_systems.length === 0 ? <p className="text-sm text-muted-foreground">None listed</p> : (
                    <ul className="space-y-0.5 text-sm">
                      {detail.source_systems.slice(0, 8).map((s) => (
                        <li key={s.name} className="flex gap-2">
                          <span className="font-medium">{s.name}</span>
                          <span className="ml-auto font-mono text-[11px] text-muted-foreground">{s.bronze_schema} · {s.skey}</span>
                        </li>
                      ))}
                      {detail.source_systems.length > 8 && <li className="text-xs text-muted-foreground">and {detail.source_systems.length - 8} more</li>}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Knowledge</p>
                  <ul className="space-y-0.5 text-sm">
                    {detail.knowledge.map((k) => (
                      <li key={k.knowledge_type} className="flex"><span>{k.knowledge_type.replace(/_/g, " ").toLowerCase()}</span><span className="ml-auto tabular-nums">{k.n}</span></li>
                    ))}
                  </ul>
                  {detail.contract && <p className="mt-2 font-mono text-[11px] text-muted-foreground">{detail.contract.split("/").pop()}</p>}
                </div>
              </div>
            )}
            {detail && (
              <DomainTools domainId={d.domain_id} name={d.domain_name} deletable={!!detail.deletable}
                           reason={detail.not_deletable_reason ?? null} activeRuns={detail.active_runs ?? []}
                           deleted={!d.active_flag} />
            )}
          </Card>
        );
      })}
    </div>
  );
}
