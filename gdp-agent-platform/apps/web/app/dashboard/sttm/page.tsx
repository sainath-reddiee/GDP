import Link from "next/link";
import { StoreNav } from "@/components/hub/store-nav";
import { scopedDomain } from "@/lib/domain-scope";
import { GitCompareArrows, History, Layers } from "lucide-react";
import { api } from "@/lib/api";
import type { SttmSummary } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { EmptyState, NotDeployed, StatusPill } from "@/components/hub/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { SttmFilters } from "./filters";

type Params = { q?: string; source?: string; target?: string; domain?: string; status?: string };

export default async function SttmCatalog({ searchParams }: { searchParams: Params }) {
  const qs = new URLSearchParams({ limit: "200" });
  const scoped = { ...searchParams, domain: scopedDomain(searchParams.domain) };  // the top bar's domain unless the URL names one
  for (const k of ["q", "source", "target", "domain", "status"] as const) {
    const v = scoped[k];
    if (v) qs.set(k, v);
  }
  const data = await api<{ mappings: SttmSummary[]; total: number; deployed: boolean }>(`/api/hub/sttm?${qs}`)
    .catch(() => ({ mappings: [] as SttmSummary[], total: 0, deployed: true }));
  const versions = data.mappings.reduce((n, m) => n + Number(m.versions || 0), 0);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Data Mapping Store"
                  description="Every source-to-target mapping with its full version history, independent of the run that produced it. Search, review rules, adjust business transformations inline and re-export as dbt models." />
      <StoreNav current="sttm" domain={searchParams.domain} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label="Mappings" value={data.total} hint="source system to target table" icon={GitCompareArrows} tone="blue" />
        <Kpi label="Versions" value={versions} hint="on record for the mappings shown" icon={History} tone="slate" />
        <Kpi label="Approved" value={data.mappings.filter((m) => m.status === "APPROVED").length} hint="latest version approved" icon={Layers} tone="emerald" />
      </div>
      {!data.deployed && <NotDeployed what="The Data Mapping catalog (CORE.STTM_CATALOG)" />}
      <SttmFilters initial={{ q: searchParams.q ?? "", source: searchParams.source ?? "", target: searchParams.target ?? "", status: searchParams.status ?? "" }} />
      {!data.mappings.length ? (
        <EmptyState icon={GitCompareArrows} title="No mappings yet">Generate a Data Mapping in a run; each version lands here automatically.</EmptyState>
      ) : (
        <div className="surface overflow-hidden">
          <Table>
            <THead><TR><TH>Target entity</TH><TH>Source</TH><TH>Domain</TH><TH className="text-right">Lines</TH><TH className="text-right">Version</TH><TH>Status</TH><TH>Last change</TH></TR></THead>
            <TBody>
              {data.mappings.map((m) => (
                <TR key={m.catalog_id}>
                  <TD><Link href={`/os/design/sttm/${m.catalog_id}`} className="font-medium text-primary hover:underline">{m.target_entity.split(".").pop()}</Link>
                    <div className="font-mono text-[11px] text-muted-foreground">{m.target_entity}</div></TD>
                  <TD className="text-xs"><span className="font-medium">{m.source_system ?? "-"}</span>
                    <div className="text-muted-foreground">{m.source_entities.slice(0, 3).join(", ")}{m.source_entities.length > 3 ? ` +${m.source_entities.length - 3}` : ""}</div></TD>
                  <TD className="text-xs">{m.domain_name ?? "-"}</TD>
                  <TD className="text-right tabular-nums">{m.line_count}</TD>
                  <TD className="text-right tabular-nums">v{m.version}<span className="text-muted-foreground"> / {m.versions}</span></TD>
                  <TD><StatusPill status={m.status} /></TD>
                  <TD className="text-xs text-muted-foreground">{m.origin.toLowerCase().replace(/_/g, " ")} · {m.created_at.slice(0, 16)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </div>
  );
}
