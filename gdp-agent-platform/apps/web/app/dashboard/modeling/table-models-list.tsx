"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import { Download, ExternalLink, KeyRound, Loader2, Plus, Search, Table2, Workflow } from "lucide-react";
import type { TmLayer, TmListRow, TmSourceTable, TmTableType } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { createTableModel, loadSourceTables } from "./table-model-actions";

const LAYERS: TmLayer[] = ["LANDING", "BRONZE", "SILVER", "GOLD"];
const TYPES: [TmTableType, string][] = [["STANDARD", "Snowflake table"], ["ICEBERG", "Iceberg table"], ["DYNAMIC_TABLE", "Dynamic table"]];

/** Git state of a table model's latest version: draft, branch, pull request or merged. */
export function GitState({ row }: { row: Pick<TmListRow, "status" | "branch" | "pr_url" | "pr_state"> }) {
  if (row.status === "PUBLISHED" || row.pr_state === "MERGED") return <StatusPill status="PUBLISHED" />;
  if (row.pr_url) return <a href={row.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">PR {row.pr_state?.toLowerCase()} <ExternalLink className="h-3 w-3" /></a>;
  if (row.branch) return <span className="font-mono text-[11px]" title={row.pr_state ?? ""}>{row.branch}</span>;
  return <span className="text-xs text-muted-foreground">Draft</span>;
}

/** Every table model: search by table, source or column; filter by domain, source, layer and run. */
export function TableModelsList({ rows, canEdit, filters }: {
  rows: TmListRow[]; canEdit: boolean;
  filters: { search: string; source: string; layer: string; run: string; domain: string };
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const toast = useToast();
  const [q, setQ] = useState(filters.search);
  const [creating, setCreating] = useState(false);
  const sources = useMemo(() => [...new Set(rows.map((r) => r.source_name))].sort(), [rows]);
  const runName = rows.find((r) => r.origin_run_id === filters.run)?.origin_run_name;

  const go = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    next.set("view", "tables");
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    router.push(`${pathname}?${next.toString()}`);
  };
  // search runs on the server (it also matches column names), a moment after typing stops
  useEffect(() => {
    if (q === filters.search) return;
    const t = setTimeout(() => go({ q }), 350);
    return () => clearTimeout(t);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-3" data-testid="table-models">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tables, sources or columns" className="pl-9" aria-label="Search table models" />
        </div>
        <Select aria-label="Source system" className="w-44" value={filters.source} onChange={(e) => go({ source: e.target.value })}>
          <option value="">All sources</option>
          {[...new Set([...sources, filters.source].filter(Boolean))].map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select aria-label="Layer" className="w-36" value={filters.layer} onChange={(e) => go({ layer: e.target.value })}>
          <option value="">All layers</option>
          {LAYERS.map((l) => <option key={l} value={l}>{l.charAt(0) + l.slice(1).toLowerCase()}</option>)}
        </Select>
        {canEdit && <Button onClick={() => setCreating(true)} data-testid="new-table-model"><Plus className="h-4 w-4" /> New table model</Button>}
      </div>
      {filters.run && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Workflow className="h-3.5 w-3.5" /> Tables from run <Link href={`/runs/${filters.run}/mapping`} className="font-medium text-primary hover:underline">{runName ?? filters.run.slice(0, 8)}</Link>
          <button type="button" onClick={() => go({ run: "" })} className="underline">show all</button>
        </p>
      )}
      {!rows.length ? (
        <EmptyState icon={Table2} title={filters.search || filters.source || filters.layer || filters.run ? "No table models match" : "No table models yet"}
                    action={canEdit ? <Button variant="outline" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New table model</Button> : undefined}>
          A run saves its model design here table by table. You can also model a landed source table directly.
        </EmptyState>
      ) : (
        <section className="surface overflow-x-auto">
          <Table>
            <THead><TR><TH>Table</TH><TH>Source</TH><TH>Layer</TH><TH>Keys</TH><TH className="text-right">Columns</TH><TH>Domain</TH><TH>Origin</TH><TH>Git</TH><TH>Updated</TH><TH /></TR></THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.table_model_id} className="align-top" data-testid={`table-model-${r.table_name}`}>
                  <TD>
                    <Link href={`/dashboard/modeling/tables/${r.table_model_id}`} className="font-mono text-sm font-semibold text-primary hover:underline">{r.table_name}</Link>
                    <p className="text-[11px] text-muted-foreground">v{r.current_version ?? "-"}{r.errors ? <span className="ml-1 text-destructive">{r.errors} error(s)</span> : null}</p>
                  </TD>
                  <TD><span className="font-mono text-xs">{r.source_name}</span>
                    {r.source_tables.length > 0 && <p className="max-w-[200px] truncate font-mono text-[11px] text-muted-foreground" title={r.source_tables.join(", ")}>{r.source_tables.join(", ")}</p>}</TD>
                  <TD className="text-xs">{r.model_layer}<p className="text-[11px] text-muted-foreground">{r.table_type.toLowerCase().replace("_", " ")}</p></TD>
                  <TD>{r.primary_keys.length ? <span className="flex items-center gap-1 font-mono text-[11px]"><KeyRound className="h-3 w-3 text-warning" />{r.primary_keys.join(", ")}</span> : <span className="text-xs text-muted-foreground">none</span>}</TD>
                  <TD className="text-right tabular-nums">{r.columns}</TD>
                  <TD className="text-xs">{r.domain_name ?? <span className="text-muted-foreground">unassigned</span>}
                    {r.linked_entity && <p className="text-[11px] text-muted-foreground">proposed as {r.linked_entity}</p>}</TD>
                  <TD>{r.origin_run_id
                    ? <button type="button" onClick={() => go({ run: r.origin_run_id ?? "" })} className="rounded-full border px-2 py-0.5 text-[11px] hover:bg-muted" title="Show this run's tables">Run: {r.origin_run_name ?? r.origin_run_id.slice(0, 8)}</button>
                    : <span className="text-[11px] text-muted-foreground">{r.origin.toLowerCase()}</span>}</TD>
                  <TD><GitState row={r} /></TD>
                  <TD className="whitespace-nowrap text-[11px] text-muted-foreground">{(r.updated_at ?? "").slice(0, 16)}<br />{r.updated_by}</TD>
                  <TD className="whitespace-nowrap">
                    <span className="flex items-center gap-2 text-xs">
                      <Link href={`/dashboard/modeling/tables/${r.table_model_id}?tab=diagram`} className="text-primary hover:underline">Canvas</Link>
                      <Link href={`/dashboard/modeling/tables/${r.table_model_id}?tab=ddl`} className="text-primary hover:underline">DDL</Link>
                      <a href={`/bff/table-model-export/${r.table_model_id}?format=dbt`} className="inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground" title="Download the dbt files"><Download className="h-3 w-3" /> dbt</a>
                    </span>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </section>
      )}
      {creating && <NewTableModel onClose={() => setCreating(false)} onCreated={(id) => { toast("ok", "Table model created"); router.push(`/dashboard/modeling/tables/${id}?tab=columns`); }} />}
    </div>
  );
}

function NewTableModel({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const toast = useToast();
  const [tables, setTables] = useState<TmSourceTable[] | null>(null);
  const [pick, setPick] = useState("");
  const [find, setFind] = useState("");
  const [name, setName] = useState("");
  const [layer, setLayer] = useState<TmLayer>("SILVER");
  const [type, setType] = useState<TmTableType>("STANDARD");
  const [pending, start] = useTransition();

  useEffect(() => {
    (async () => {
      const r = await loadSourceTables();
      if (r.ok) setTables(r.data.tables); else { setTables([]); toast("error", explain(r.error).message); }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = (tables ?? []).filter((t) => !find || `${t.source_name} ${t.source_table}`.toLowerCase().includes(find.toLowerCase()));
  const chosen = tables?.find((t) => t.landing_id === pick);
  const create = () => start(async () => {
    if (!chosen) return;
    const r = await createTableModel({ landing_id: chosen.landing_id, table_name: name.trim() || undefined, model_layer: layer, table_type: type });
    if (r.ok) onCreated(r.data.table_model_id); else toast("error", explain(r.error).message);
  });

  return (
    <Modal open onClose={onClose} title="New table model" wide
           description="Model a landed source table: its columns, types and comments become version 1, which you then shape in the workbench."
           footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={create} disabled={pending || !chosen || chosen.exists}>{pending && <Loader2 className="h-4 w-4 animate-spin" />} Create</Button></>}>
      <div className="space-y-3">
        <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Filter source tables" aria-label="Filter source tables" />
        <div className="max-h-64 overflow-y-auto rounded-md border">
          {tables === null ? <p className="p-3 text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> Loading source tables...</p>
            : !shown.length ? <p className="p-3 text-sm text-muted-foreground">No landed source tables{find ? " match" : " yet; onboard a source first"}.</p>
            : shown.map((t) => (
              <label key={t.landing_id} className="flex cursor-pointer items-center gap-2 border-b px-3 py-1.5 text-sm last:border-b-0 hover:bg-muted/50">
                <input type="radio" name="source-table" checked={pick === t.landing_id} disabled={t.exists} onChange={() => { setPick(t.landing_id); setName(t.source_table.toUpperCase()); }} />
                <span className="font-mono text-xs">{t.source_name}</span>
                <span className="font-mono">{t.source_table}</span>
                <span className="ml-auto text-[11px] text-muted-foreground">{t.exists ? "already modeled" : `${t.source_database}.${t.source_schema}`}</span>
              </label>
            ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div><Label htmlFor="tm-name">Table name</Label><Input id="tm-name" className="font-mono" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="CUSTOMER" /></div>
          <div><Label htmlFor="tm-layer">Layer</Label>
            <Select id="tm-layer" value={layer} onChange={(e) => setLayer(e.target.value as TmLayer)}>{LAYERS.map((l) => <option key={l} value={l}>{l}</option>)}</Select></div>
          <div><Label htmlFor="tm-type">Table type</Label>
            <Select id="tm-type" value={type} onChange={(e) => setType(e.target.value as TmTableType)}>{TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></div>
        </div>
      </div>
    </Modal>
  );
}
