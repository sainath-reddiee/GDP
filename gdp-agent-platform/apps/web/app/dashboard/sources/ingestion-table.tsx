"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { ChevronRight, Loader2, Plus } from "lucide-react";
import type { IngestionControl, IngestionHistory } from "@/lib/hub-types";
import { Drawer } from "@/components/hub/overlay";
import { explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { editIngestion, loadIngestion } from "./actions";

/** SnapLogic ingestion configurations (PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG), one group per source (master ID)
 * with a row per table; a row opens its full configuration and history, and its switch turns it on or off. */
export function IngestionTable({ controls: initial }: { controls: IngestionControl[] }) {
  const toast = useToast();
  const [controls, setControls] = useState(initial);
  // a few sources start expanded; with many, only the newest
  const [open, setOpen] = useState<Record<number, boolean>>(() => {
    const masters = Array.from(new Set(initial.map((c) => c.master_id)));
    return Object.fromEntries((masters.length <= 5 ? masters : masters.slice(0, 1)).map((m) => [m, true]));
  });
  const [detail, setDetail] = useState<{ row: IngestionControl; history: IngestionHistory[] | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [, start] = useTransition();
  const groups = useMemo(() => {
    const by = new Map<number, IngestionControl[]>();
    for (const c of controls) by.set(c.master_id, [...(by.get(c.master_id) ?? []), c]);
    return Array.from(by.entries());
  }, [controls]);

  const toggle = (c: IngestionControl) => start(async () => {
    const key = `${c.master_id}-${c.child_id}`;
    setBusy(key);
    const r = await editIngestion(c.master_id, c.child_id, { is_active: !c.is_active });
    setBusy(null);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if ("pending_approval" in r.data) { toast("info", r.data.detail); return; }
    const on = r.data.is_active;
    setControls((cs) => cs.map((x) => (x.master_id === c.master_id && x.child_id === c.child_id ? { ...x, is_active: on } : x)));
    setDetail((d) => (d && d.row.child_id === c.child_id ? { ...d, row: { ...d.row, is_active: on }, history: null } : d));
    toast("ok", `${c.landing_table} ${on ? "activated" : "deactivated"}; SnapLogic ${on ? "loads it on its next run" : "skips it from now on"}.`);
  });
  const show = (c: IngestionControl) => start(async () => {
    setDetail({ row: c, history: null });
    const r = await loadIngestion(c.master_id);
    if (r.ok) setDetail({ row: r.data.controls.find((x) => x.child_id === c.child_id) ?? c, history: r.data.history.filter((h) => h.child_id === c.child_id) });
  });

  if (!controls.length) {
    return (
      <div className="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground" data-testid="ingestion-empty">
        No SnapLogic ingestion configured yet.{" "}
        <Link href="/dashboard/onboarding?type=source&branch=external" className="font-medium text-primary hover:underline">Onboard an external source</Link>
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="ingestion-table">
      {groups.map(([master, rows]) => {
        const first = rows[0];
        const active = rows.filter((r) => r.is_active).length;
        return (
          <section key={master} className="overflow-hidden rounded-xl border bg-card" data-testid={`master-${master}`}>
            <button type="button" onClick={() => setOpen((o) => ({ ...o, [master]: !o[master] }))} aria-expanded={!!open[master]}
                    className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left">
              <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition", open[master] && "rotate-90")} />
              <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">master {master}</span>
              <span className="text-sm font-medium">{first.source_name ?? first.parent_pipeline_name}</span>
              {first.source_type && <span className="text-xs text-muted-foreground">{first.source_type}</span>}
              <span className="text-xs text-muted-foreground">{first.parent_pipeline_name} → {first.child_pipeline_name}</span>
              <span className="flex-1" />
              <span className="text-xs text-muted-foreground">{rows.length} table{rows.length === 1 ? "" : "s"} · {active} active</span>
              <span className="text-xs text-muted-foreground">{first.created_by} · {first.created_at?.slice(0, 16)}</span>
            </button>
            {open[master] && (
              <div className="overflow-x-auto border-t">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted/40 text-[10px] uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Child</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Landing → bronze</th>
                      <th className="px-3 py-2">Load</th><th className="px-3 py-2">Schedule</th><th className="px-3 py-2">Last run</th>
                      <th className="px-3 py-2">Active</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {rows.map((c) => (
                      <tr key={c.child_id} className="cursor-pointer hover:bg-muted/30" onClick={() => show(c)} data-testid={`child-${c.child_id}`}>
                        <td className="px-3 py-2 font-mono">{c.child_id}</td>
                        <td className="max-w-[220px] truncate px-3 py-2 font-mono" title={c.source_table_sql}>{c.source_table_sql}</td>
                        <td className="px-3 py-2 font-mono">{c.landing_table} → {c.bronze_schema}.{c.bronze_table}</td>
                        <td className="px-3 py-2">{c.load_type.toLowerCase()} / {c.bronze_load_type.toLowerCase()}</td>
                        <td className="px-3 py-2">{c.load_frequency === "ON_DEMAND" ? "on demand" : <span className="font-mono">{c.job_trigger_time}</span>}</td>
                        <td className="px-3 py-2">{c.last_run_timestamp ? `${c.last_run_timestamp.slice(0, 16)}${c.completion_flag ? " · complete" : " · incomplete"}` : "not run yet"}</td>
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          <button type="button" role="switch" aria-checked={c.is_active} aria-label={`${c.landing_table} active`}
                                  disabled={busy === `${c.master_id}-${c.child_id}`} onClick={() => toggle(c)}
                                  className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition", c.is_active ? "bg-success" : "bg-muted-foreground/30")}>
                            {busy === `${c.master_id}-${c.child_id}` ? <Loader2 className="mx-auto h-3 w-3 animate-spin text-white" />
                              : <span className={cn("inline-block h-4 w-4 rounded-full bg-white shadow transition", c.is_active ? "translate-x-4" : "translate-x-0.5")} />}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
      <Link href="/dashboard/onboarding?type=source&branch=external" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
        <Plus className="h-3.5 w-3.5" /> Onboard another external source
      </Link>
      <Drawer open={!!detail} onClose={() => setDetail(null)} wide
              title={detail ? <span className="font-mono">{detail.row.landing_table}</span> : ""}
              subtitle={detail ? `master ${detail.row.master_id} · child ${detail.row.child_id} · ${detail.row.is_active ? "active" : "inactive"}` : ""}>
        {detail && <ControlDetail row={detail.row} history={detail.history} />}
      </Drawer>
    </div>
  );
}

const GROUPS: { title: string; keys: (keyof IngestionControl)[] }[] = [
  { title: "SnapLogic", keys: ["snaplogic_project_name", "parent_pipeline_name", "child_pipeline_name", "api_url", "api_key"] },
  { title: "Source", keys: ["source_account", "source_database_path", "source_schema_file", "source_file_type", "source_sheet_name",
                            "source_table_sql", "source_where_clause", "source_delta_key", "source_primary_keys", "source_audit_columns"] },
  { title: "Target", keys: ["target_account", "landing_database", "landing_schema", "landing_table", "bronze_schema", "bronze_table",
                            "external_stage", "external_volume", "base_location", "config_table"] },
  { title: "Load", keys: ["load_type", "bronze_load_type", "load_frequency", "job_trigger_time", "landing_to_bronze_flag", "is_active"] },
  { title: "Source deletes", keys: ["source_delete_action", "source_delete_key", "source_delete_key_value", "source_delete_query"] },
  { title: "Set by SnapLogic", keys: ["target_previous_load_time", "last_run_timestamp", "completion_flag"] },
  { title: "Audit", keys: ["created_by", "created_at", "updated_by", "updated_at", "comments"] },
];

function ControlDetail({ row, history }: { row: IngestionControl; history: IngestionHistory[] | null }) {
  const show = (v: unknown) => (v === null || v === undefined || v === "" ? "-" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v));
  return (
    <div className="space-y-5 text-xs" data-testid="ingestion-detail">
      {GROUPS.map((g) => (
        <section key={g.title}>
          <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{g.title}</h4>
          <dl className="grid grid-cols-[180px_1fr] gap-x-3 gap-y-1">
            {g.keys.map((k) => (
              <div key={k} className="contents">
                <dt className="font-mono text-[11px] text-muted-foreground">{k.toUpperCase()}</dt>
                <dd className={cn("break-words", /sql|query|clause/.test(k) && "font-mono")}>{show(row[k])}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <section>
        <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">History</h4>
        {history === null ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Loading…</p>
          : !history.length ? <p className="text-muted-foreground">No changes recorded.</p> : (
            <ul className="space-y-1" data-testid="ingestion-history">
              {history.map((h, i) => (
                <li key={i} className="flex gap-2"><span className="font-medium">{h.action.toLowerCase()}</span>
                  <span className="text-muted-foreground">{h.changed_by} · {h.changed_at.slice(0, 16)} · {h.is_active ? "active" : "inactive"}</span></li>
              ))}
            </ul>
          )}
      </section>
    </div>
  );
}
