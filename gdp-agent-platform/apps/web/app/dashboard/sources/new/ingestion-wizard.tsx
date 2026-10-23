"use client";

import Link from "next/link";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import { AlertCircle, Check, CheckCircle2, Copy, ListChecks, Loader2, Plus, Save, Trash2 } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { explain } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { saveIngestion, snaplogicPreview, snaplogicStatus, validateIngestion, type IngestionPayload } from "../actions";
import { SnapLogicRows } from "@/components/sources/snaplogic-preview";
import type { SnapLogicOutcome, SnapLogicPreview, SnapLogicStatus } from "@/lib/hub-types";
import type { DomainOption, SourceKind } from "./wizard";

// each kind's source types (services/source/ingestion_control.py SOURCE_KINDS), PostgreSQL and S3 first
const KIND_TYPES: Record<SourceKind, { value: string; label: string }[]> = {
  database: [{ value: "POSTGRESQL", label: "PostgreSQL" }, { value: "ORACLE", label: "Oracle" }, { value: "SQLSERVER", label: "SQL Server" }],
  file: [{ value: "S3", label: "Amazon S3" }, { value: "SHAREPOINT", label: "SharePoint" }],
};
const FILE_FORMATS = [{ value: "CSV", label: "CSV" }, { value: "JSON", label: "JSON" }, { value: "PARQUET", label: "Parquet" },
  { value: "XLSX", label: "Excel (XLSX)" }];
const LOAD_TYPES = ["APPEND", "TRUNCATE", "SCD2"];
const BRONZE_LOAD_TYPES = ["TRUNCATE", "SCD2", "MERGE"];
const FREQUENCIES = ["HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND"];

type Table = {
  source_table_sql: string; landing_table: string; bronze_table: string; source_delta_key: string; source_primary_keys: string;
  source_where_clause: string; source_audit_columns: string; deletes: boolean; source_delete_key: string;
  source_delete_key_value: string; source_delete_query: string;
};
const EMPTY_TABLE: Table = {
  source_table_sql: "", landing_table: "", bronze_table: "", source_delta_key: "", source_primary_keys: "", source_where_clause: "",
  source_audit_columns: "", deletes: false, source_delete_key: "", source_delete_key_value: "", source_delete_query: "",
};

const STEPS = [
  { id: "source", title: "Source", hint: "The system and where it is read from" },
  { id: "tables", title: "Tables", hint: "What to read and how to track changes" },
  { id: "target", title: "Target", hint: "Landing and bronze in Snowflake" },
  { id: "load", title: "Load", hint: "Strategy and schedule" },
  { id: "snaplogic", title: "SnapLogic", hint: "Project and pipelines" },
  { id: "review", title: "Review", hint: "The rows Check found valid, as they will be saved" },
] as const;

function Field({ label, error, help, required, children, id, col }: {
  label: string; error?: string; help?: ReactNode; required?: boolean; children: ReactNode; id: string; col?: string;
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="flex items-baseline gap-2">
        <span>{label}{required && <span className="text-destructive"> *</span>}</span>
        {col && <span className="font-mono text-[10px] font-normal text-muted-foreground">{col}</span>}
      </Label>
      {children}
      {error ? <p className="text-xs text-destructive" role="alert">{error}</p> : help ? <p className="text-xs text-muted-foreground">{help}</p> : null}
    </div>
  );
}

/** One part of the form: its title, how many of its fields need fixing, and its fields. */
function WizardSection({ id, errors, children }: { id: (typeof STEPS)[number]["id"]; errors: number; children: ReactNode }) {
  const meta = STEPS.find((s) => s.id === id)!;
  return (
    <section id={`ingest-${id}`} data-testid={`section-${id}`} className="scroll-mt-32 space-y-3 border-t pt-5 first:border-t-0 first:pt-0">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold">{meta.title}
          {errors > 0 && <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[11px] font-medium text-destructive">{errors} to fix</span>}</h3>
        <p className="text-xs text-muted-foreground">{meta.hint}</p>
      </div>
      {children}
    </section>
  );
}

function Toggle({ id, checked, onChange, label, help, col }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string; help?: string; col?: string }) {
  return (
    <label htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-xl border bg-card p-3">
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]" />
      <span>
        <span className="flex items-baseline gap-2 text-sm font-medium">{label}{col && <span className="font-mono text-[10px] font-normal text-muted-foreground">{col}</span>}</span>
        {help && <span className="block text-xs text-muted-foreground">{help}</span>}
      </span>
    </label>
  );
}

/** Onboard other sources for SnapLogic, a relational database (PostgreSQL, Oracle, SQL Server) or files (Amazon S3,
 * SharePoint), each kind with its own questions (the onboarding configuration workbook): one page with the defaults
 * filled in, checked as a whole by the API (every problem shown where it is, the review once nothing is); saving writes one catalog row for the source and one control row per table
 * (CORE.INGESTION_CONTROL_CONFIG) under one master ID, and SnapLogic's AODS rows when the sync is on. */
export function IngestionWizard({ kind, domains, user }: { kind: SourceKind; domains: DomainOption[]; user: string | null }) {
  const toast = useToast();
  const files = kind === "file";
  // the form as it was when Check last passed; any change after that hides the review until the next check
  const [checkedFor, setCheckedFor] = useState<string | null>(null);
  // whether Check has run at all (sections without problems then show as done), and the section in view
  const [checkedOnce, setCheckedOnce] = useState(false);
  const [active, setActive] = useState<string>("source");
  const [source, setSource] = useState({ source_name: "", source_type: KIND_TYPES[kind][0].value, domain_id: "", source_account: "",
                                         source_database_path: "", source_schema_file: "", source_file_type: files ? "CSV" : "TABLE",
                                         source_sheet_name: "", source_system_desc: "", airflow_source_system: "", silver_command_name: "" });
  const [tables, setTables] = useState<Table[]>([{ ...EMPTY_TABLE }]);
  const [target, setTarget] = useState({ target_account: "", landing_database: "", landing_schema: "LANDING", bronze_schema: "BRONZE", bronze_database: "",
                                         external_stage: "", iceberg: false, external_volume: "", base_location: "", config_table: "" });
  const [load, setLoad] = useState({ load_type: "APPEND", bronze_load_type: "TRUNCATE", load_frequency: "DAILY",
                                     job_trigger_time: "0 2 * * * UTC", landing_to_bronze_flag: true, is_active: true,
                                     last_run_timestamp: "", completion_flag: false });
  const [snap, setSnap] = useState({ snaplogic_project_name: "", parent_pipeline_name: "", child_pipeline_name: "" });
  const [comments, setComments] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Record<string, unknown>[]>([]);
  const [saved, setSaved] = useState<{ master_id: number; tables: number; parent: string; snaplogic?: SnapLogicOutcome } | null>(null);
  const [busy, start] = useTransition();
  // SnapLogic's config tables (Admin, SNAPLOGIC_SYNC): when the sync is on, the live defaults are filled in, the child
  // pipeline follows the source type until someone types their own, and the review shows the rows to be written
  const [sync, setSync] = useState<SnapLogicStatus | null>(null);
  const [childTyped, setChildTyped] = useState(false);
  const [snapRows, setSnapRows] = useState<SnapLogicPreview | null>(null);
  useEffect(() => {
    void snaplogicStatus().then((r) => {
      if (!r.ok || !r.data.enabled) return;
      const d = r.data.defaults;
      setSync(r.data);
      setSnap((s) => ({ ...s, snaplogic_project_name: s.snaplogic_project_name || String(d.snaplogic_project_name ?? ""),
                        parent_pipeline_name: s.parent_pipeline_name || String(d.parent_pipeline_name ?? ""),
                        child_pipeline_name: s.child_pipeline_name || (r.data.child_pipeline_by_type[source.source_type] ?? "") }));
      setTarget((t) => ({ ...t, target_account: t.target_account || String(d.target_account ?? ""),
                          bronze_database: t.bronze_database || String(d.bronze_database ?? "") }));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const pickType = (type: string) => {
    setSource({ ...source, source_type: type });
    if (sync && !childTyped) {
      const kind = type === "POSTGRES" ? "POSTGRESQL" : type;
      // no mapping for this type: empty, so the previous type's pipeline is never saved for it
      setSnap((s) => ({ ...s, child_pipeline_name: sync.child_pipeline_by_type[kind] ?? "" }));
    }
  };

  const payload = (): IngestionPayload => ({
    source: { ...source, source_kind: files ? "FILE" : "DATABASE" }, target, load, snaplogic: snap, comments,
    tables: tables.map((t) => ({ ...t, source_delete_action: t.deletes ? "REQUIRED" : "NOT_REQUIRED" })),
  });
  const err = (path: string) => errors[path];
  const checked = checkedFor === JSON.stringify(payload());
  // which section an error belongs to (its path starts with the section's id: source., tables.0., load., ...)
  const sectionOf = (path: string) => STEPS.find((s) => path === s.id || path.startsWith(`${s.id}.`))?.id ?? "source";
  const count = (id: string) => Object.keys(errors).filter((k) => sectionOf(k) === id).length;
  const scrollTo = (id: string) =>
    window.setTimeout(() => document.getElementById(`ingest-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  const fixCount = Object.keys(errors).length;
  // the section bar follows the scroll: the section nearest the top of the screen, under the two sticky bars
  useEffect(() => {
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => seen.set(e.target.id.replace("ingest-", ""), e.isIntersecting));
      const first = STEPS.find((s) => seen.get(s.id));
      if (first) setActive(first.id);
    }, { rootMargin: "-120px 0px -55% 0px" });
    STEPS.forEach((s) => { const el = document.getElementById(`ingest-${s.id}`); if (el) io.observe(el); });
    return () => io.disconnect();
  }, [checked, saved]);

  // one check of the whole form: every problem is shown where it is, and the page goes to the first one; when
  // nothing is wrong the review appears with the rows to be saved
  const check = () => start(async () => {
    const r = await validateIngestion(payload());
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    setErrors(r.data.errors);
    setCheckedOnce(true);
    const keys = Object.keys(r.data.errors);
    if (keys.length) {
      setCheckedFor(null);
      scrollTo(sectionOf(keys[0]));
      return;
    }
    setPreview(r.data.rows);
    if (sync) {
      const rows = await snaplogicPreview(payload());
      setSnapRows(rows.ok ? rows.data : null);
    }
    setCheckedFor(JSON.stringify(payload()));
    scrollTo("review");
  });
  const save = () => start(async () => {
    const r = await saveIngestion(payload());
    if (!r.ok) {
      const e = explain(r.error);
      setErrors(e.fields);
      const first = Object.keys(e.fields)[0];
      if (first) scrollTo(sectionOf(first));
      toast("error", e.message);
      return;
    }
    if ("pending_approval" in r.data) { toast("info", r.data.detail); return; }
    const d = r.data;
    toast("ok", `Configuration saved: master ID ${d.master_id}, ${d.children.length} table${d.children.length === 1 ? "" : "s"} registered for ${d.parent_pipeline_name}.`);
    setSaved({ master_id: d.master_id, tables: d.children.length, parent: d.parent_pipeline_name, snaplogic: d.snaplogic });
  });
  const setTable = (i: number, patch: Partial<Table>) => setTables((ts) => ts.map((t, k) => (k === i ? { ...t, ...patch } : t)));

  if (saved) {
    return (
      <div className="surface flex flex-col items-center gap-3 px-6 py-12 text-center" data-testid="ingestion-saved">
        <CheckCircle2 className="h-10 w-10 text-success" />
        <h3 className="text-base font-semibold">Configuration saved</h3>
        <p className="max-w-md text-sm text-muted-foreground">
          Master ID {saved.master_id} with {saved.tables} table{saved.tables === 1 ? "" : "s"} is in CORE.INGESTION_CONTROL_CONFIG.
          {" "}{saved.parent} picks it up on its next run{load.is_active ? "" : " once the rows are switched on"}.
        </p>
        {saved.snaplogic?.master_config_id != null && (
          <p className="max-w-md text-sm text-muted-foreground" data-testid="snaplogic-saved">
            SnapLogic: master row {saved.snaplogic.master_config_id}{saved.snaplogic.master_created ? " (new)" : " (reused)"} in{" "}
            <span className="font-mono text-xs">{saved.snaplogic.tables?.master}</span>, detail rows{" "}
            {(saved.snaplogic.child_config_ids ?? []).join(", ")} in <span className="font-mono text-xs">{saved.snaplogic.tables?.detail}</span>.
          </p>
        )}
        <div className="flex gap-2">
          <Link href="/dashboard/sources" className={buttonVariants()}>Open in the Source Store</Link>
          <Button variant="outline" onClick={() => { setSaved(null); setCheckedFor(null); setCheckedOnce(false); setTables([{ ...EMPTY_TABLE }]); setPreview([]); }}>Onboard another</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="surface overflow-clip" data-testid="ingestion-wizard">
      {/* one bar under the app header: where you are, what each section needs, and the actions, always in reach and
          never over a field */}
      <div className="sticky top-[57px] z-20 flex items-center gap-2 border-b bg-card/95 px-2 py-2 backdrop-blur">
        <nav className="flex min-w-0 [mask-image:linear-gradient(to_right,black_88%,transparent)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden flex-1 gap-1 overflow-x-auto" aria-label="Sections">
          {STEPS.filter((s) => s.id !== "review" || checked).map((s, i) => {
            const n = count(s.id);
            const ok = checked || (checkedOnce && n === 0);
            return (
              <button key={s.id} type="button" onClick={() => scrollTo(s.id)} data-testid={`go-${s.id}`}
                      aria-current={active === s.id ? "location" : undefined}
                      className={cn("flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition",
                        active === s.id ? "bg-primary/10 font-medium text-foreground ring-1 ring-primary/30" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                <span className={cn("grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold",
                  n > 0 ? "bg-destructive text-white" : ok ? "bg-success text-white" : "bg-muted text-muted-foreground")}>
                  {n > 0 ? n : ok ? <Check className="h-2.5 w-2.5" strokeWidth={3} /> : i + 1}
                </span>
                {s.title}
              </button>
            );
          })}
        </nav>
        <Button size="sm" variant="outline" disabled={busy} onClick={check} data-testid="check-ingestion" title="Check every field">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}<span className="hidden sm:inline">Check</span>
        </Button>
        <Button size="sm" disabled={busy} onClick={save} data-testid="save-ingestion" title="Save configuration">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}<span className="hidden sm:inline">Save</span>
        </Button>
      </div>
      <div className="space-y-6 p-5">
        {fixCount > 0 && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-xs" data-testid="ingestion-problems">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span>
              <span className="font-medium text-destructive">{fixCount} field{fixCount === 1 ? "" : "s"} to fix</span>
              {" "}in {STEPS.filter((s) => count(s.id) > 0).map((s, i, all) => (
                <span key={s.id}>
                  <button type="button" className="underline underline-offset-2 hover:text-foreground" onClick={() => scrollTo(s.id)}>{s.title}</button>
                  {i < all.length - 2 ? ", " : i === all.length - 2 ? " and " : ""}
                </span>
              ))}. Each one is marked in red below; check again when done.
            </span>
          </div>
        )}

        <WizardSection id="source" errors={count("source")}>
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="source_name" label="Source name" required error={err("source.source_name")} help="Letters, digits and _; names the landing tables">
              <Input id="source_name" value={source.source_name} placeholder={files ? "CRM_FILES" : "CRM_POSTGRES"} onChange={(e) => setSource({ ...source, source_name: e.target.value })} />
            </Field>
            <Field id="source_type" col="SOURCE_TYPE" label="Source type" required error={err("source.source_type")}>
              <Select id="source_type" value={source.source_type} onChange={(e) => pickType(e.target.value)}>
                {KIND_TYPES[kind].map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </Select>
            </Field>
            <Field id="source_account" col="SOURCE_ACCOUNT" label="Source account" required error={err("source.source_account")} help="The SnapLogic account that connects to the source">
              <Input id="source_account" value={source.source_account} placeholder={files ? "crm_s3_exports" : "crm_postgres_prod"} onChange={(e) => setSource({ ...source, source_account: e.target.value })} />
            </Field>
            <Field id="domain" col="DOMAIN" label="Domain" required error={err("source.domain_id")}>
              <Select id="domain" value={source.domain_id} onChange={(e) => setSource({ ...source, domain_id: e.target.value })}>
                <option value="">Choose the domain</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select>
            </Field>
            <Field id="source_database_path" col="SOURCE_DATABASE_PATH" label={files ? "Source path" : "Source database"} required error={err("source.source_database_path")}
                   help={files ? "The bucket folder or SharePoint site and folder" : "The database or service name"}>
              <Input id="source_database_path" value={source.source_database_path} placeholder={files ? "s3://crm-exports/daily/" : "CRMPROD"}
                     onChange={(e) => setSource({ ...source, source_database_path: e.target.value })} />
            </Field>
            <Field id="source_schema_file" col="SOURCE_SCHEMA_FILE" label={files ? "Source file" : "Source schema"} required error={err("source.source_schema_file")}
                   help={files ? "A file name or pattern" : undefined}>
              <Input id="source_schema_file" value={source.source_schema_file} placeholder={files ? "customers_*.csv" : "SALES"}
                     onChange={(e) => setSource({ ...source, source_schema_file: e.target.value })} />
            </Field>
            {files && (
              <Field id="source_file_type" col="SOURCE_FILE_TYPE" label="File type" required error={err("source.source_file_type")}>
                <Select id="source_file_type" value={source.source_file_type} onChange={(e) => setSource({ ...source, source_file_type: e.target.value })}>
                  {FILE_FORMATS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </Select>
              </Field>
            )}
            {files && source.source_file_type === "XLSX" && (
              <Field id="source_sheet_name" col="SOURCE_SHEET_NAME" label="Sheet name" required error={err("source.source_sheet_name")}>
                <Input id="source_sheet_name" value={source.source_sheet_name} placeholder="Customers" onChange={(e) => setSource({ ...source, source_sheet_name: e.target.value })} />
              </Field>
            )}
            {sync && (
              <>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground md:col-span-2">For SnapLogic&apos;s master row</p>
                <Field id="source_system_desc" col="SOURCE_SYSTEM_DESC" label="Description" error={err("source.source_system_desc")}>
                  <Input id="source_system_desc" value={source.source_system_desc} placeholder="CRM accounts and contacts" onChange={(e) => setSource({ ...source, source_system_desc: e.target.value })} />
                </Field>
                <Field id="airflow_source_system" col="AIRFLOW_SOURCE_SYSTEM" label="Airflow source system" error={err("source.airflow_source_system")}>
                  <Input id="airflow_source_system" value={source.airflow_source_system} placeholder="crm" onChange={(e) => setSource({ ...source, airflow_source_system: e.target.value })} />
                </Field>
                <Field id="silver_command_name" col="SILVER_COMMAND_NAME" label="Silver command" error={err("source.silver_command_name")}>
                  <Input id="silver_command_name" value={source.silver_command_name} onChange={(e) => setSource({ ...source, silver_command_name: e.target.value })} />
                </Field>
              </>
            )}
          </div>
        </WizardSection>

        <WizardSection id="tables" errors={count("tables")}>
          <div className="space-y-3">
            {err("tables") && <p className="text-xs text-destructive" role="alert">{err("tables")}</p>}
            {tables.map((t, i) => (
              <section key={i} className="space-y-3 rounded-xl border p-4" data-testid={`table-${i}`}>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Table {i + 1}</span>
                  <span className="flex-1" />
                  <Button size="sm" variant="ghost" onClick={() => setTables((ts) => [...ts.slice(0, i + 1), { ...t }, ...ts.slice(i + 1)])} aria-label={`Copy table ${i + 1}`}>
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                  {tables.length > 1 && (
                    <Button size="sm" variant="ghost" onClick={() => setTables((ts) => ts.filter((_, k) => k !== i))} aria-label={`Remove table ${i + 1}`}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
                {files ? (
                  <Field id={`sql-${i}`} col="SOURCE_TABLE_SQL" label="Table name" required error={err(`tables.${i}.source_table_sql`)}
                         help="The name the file's rows load as, such as CUSTOMERS">
                    <Input id={`sql-${i}`} className="font-mono text-xs" value={t.source_table_sql} placeholder="CUSTOMERS"
                           onChange={(e) => setTable(i, { source_table_sql: e.target.value })} />
                  </Field>
                ) : (
                  <Field id={`sql-${i}`} col="SOURCE_TABLE_SQL" label="Source table or query" required error={err(`tables.${i}.source_table_sql`)}
                         help="A table name (SALES.CUSTOMERS) or one read-only SELECT">
                    <Textarea id={`sql-${i}`} rows={2} className="font-mono text-xs" value={t.source_table_sql} placeholder="SALES.CUSTOMERS"
                              onChange={(e) => setTable(i, { source_table_sql: e.target.value })} />
                  </Field>
                )}
                <div className="grid gap-3 md:grid-cols-3">
                  <Field id={`delta-${i}`} col="SOURCE_DELTA_KEY" label="Delta key" error={err(`tables.${i}.source_delta_key`)} help="Column that marks new or changed rows">
                    <Input id={`delta-${i}`} value={t.source_delta_key} placeholder="UPDATED_AT" onChange={(e) => setTable(i, { source_delta_key: e.target.value })} />
                  </Field>
                  <Field id={`pk-${i}`} col="SOURCE_PRIMARY_KEYS" label="Primary keys" error={err(`tables.${i}.source_primary_keys`)}
                         required={!files || load.load_type === "SCD2" || ["SCD2", "MERGE"].includes(load.bronze_load_type)}
                         help={files ? "Comma-separated; needed for SCD2 and MERGE loads" : "Comma-separated"}>
                    <Input id={`pk-${i}`} value={t.source_primary_keys} placeholder="CUSTOMER_ID" onChange={(e) => setTable(i, { source_primary_keys: e.target.value })} />
                  </Field>
                  <Field id={`audit-${i}`} col="SOURCE_AUDIT_COLUMNS" label="Audit columns" error={err(`tables.${i}.source_audit_columns`)} help="Comma-separated">
                    <Input id={`audit-${i}`} value={t.source_audit_columns} placeholder="CREATED_AT, UPDATED_AT" onChange={(e) => setTable(i, { source_audit_columns: e.target.value })} />
                  </Field>
                </div>
                <Field id={`where-${i}`} col="SOURCE_WHERE_CLAUSE" label="Filter" error={err(`tables.${i}.source_where_clause`)} help="A condition without WHERE, such as STATUS <> 'TEST'">
                  <Input id={`where-${i}`} className="font-mono text-xs" value={t.source_where_clause} onChange={(e) => setTable(i, { source_where_clause: e.target.value })} />
                </Field>
                <div className="grid gap-3 md:grid-cols-2">
                  <Field id={`landing-${i}`} col="LANDING_TABLE" label="Landing table" error={err(`tables.${i}.landing_table`)} help="Leave empty for SOURCE__TABLE">
                    <Input id={`landing-${i}`} value={t.landing_table} onChange={(e) => setTable(i, { landing_table: e.target.value })} />
                  </Field>
                  <Field id={`bronze-${i}`} col="BRONZE_TABLE" label="Bronze table" error={err(`tables.${i}.bronze_table`)} help="Leave empty to use the landing table name">
                    <Input id={`bronze-${i}`} value={t.bronze_table} onChange={(e) => setTable(i, { bronze_table: e.target.value })} />
                  </Field>
                </div>
                <Toggle id={`deletes-${i}`} checked={t.deletes} onChange={(v) => setTable(i, { deletes: v })} label="Source deletes rows" col="SOURCE_DELETE_ACTION"
                        help="Turn on when deleted source rows must be removed or flagged downstream" />
                {t.deletes && (
                  <div className="grid gap-3 md:grid-cols-3" data-testid={`deletes-${i}`}>
                    <Field id={`dkey-${i}`} col="SOURCE_DELETE_KEY" label="Delete key" required error={err(`tables.${i}.source_delete_key`)}>
                      <Input id={`dkey-${i}`} value={t.source_delete_key} placeholder="IS_DELETED" onChange={(e) => setTable(i, { source_delete_key: e.target.value })} />
                    </Field>
                    <Field id={`dvalue-${i}`} col="SOURCE_DELETE_KEY_VALUE" label="Delete key value" error={err(`tables.${i}.source_delete_key_value`)}>
                      <Input id={`dvalue-${i}`} value={t.source_delete_key_value} placeholder="Y" onChange={(e) => setTable(i, { source_delete_key_value: e.target.value })} />
                    </Field>
                    <Field id={`dquery-${i}`} col="SOURCE_DELETE_QUERY" label="Delete query" error={err(`tables.${i}.source_delete_query`)}>
                      <Input id={`dquery-${i}`} className="font-mono text-xs" value={t.source_delete_query} onChange={(e) => setTable(i, { source_delete_query: e.target.value })} />
                    </Field>
                  </div>
                )}
              </section>
            ))}
            <Button variant="outline" size="sm" onClick={() => setTables((ts) => [...ts, { ...EMPTY_TABLE }])}><Plus className="h-3.5 w-3.5" /> Add table</Button>
          </div>
        </WizardSection>

        <WizardSection id="target" errors={count("target")}>
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="landing_database" col="LANDING_DATABASE" label="Landing database" required error={err("target.landing_database")}>
              <Input id="landing_database" value={target.landing_database} placeholder="DEV_GDP_LANDING_DB" onChange={(e) => setTarget({ ...target, landing_database: e.target.value })} />
            </Field>
            <Field id="landing_schema" col="LANDING_SCHEMA" label="Landing schema" required error={err("target.landing_schema")}>
              <Input id="landing_schema" value={target.landing_schema} onChange={(e) => setTarget({ ...target, landing_schema: e.target.value })} />
            </Field>
            <Field id="bronze_schema" col="BRONZE_SCHEMA" label="Bronze schema" required error={err("target.bronze_schema")}>
              <Input id="bronze_schema" value={target.bronze_schema} onChange={(e) => setTarget({ ...target, bronze_schema: e.target.value })} />
            </Field>
            {sync && (
              <Field id="bronze_database" col="BRONZE_DATABASE" label="Bronze database" error={err("target.bronze_database")}>
                <Input id="bronze_database" value={target.bronze_database} placeholder="DEV_GDP_BRONZE_DB" onChange={(e) => setTarget({ ...target, bronze_database: e.target.value })} />
              </Field>
            )}
            <Field id="target_account" col="TARGET_ACCOUNT" label="Target account" error={err("target.target_account")} help="The SnapLogic account that writes to Snowflake">
              <Input id="target_account" value={target.target_account} placeholder="snowflake_ingest" onChange={(e) => setTarget({ ...target, target_account: e.target.value })} />
            </Field>
            <Field id="external_stage" col="EXTERNAL_STAGE" label="External stage" error={err("target.external_stage")}>
              <Input id="external_stage" value={target.external_stage} placeholder="@LANDING.EXT_STAGE/crm" onChange={(e) => setTarget({ ...target, external_stage: e.target.value })} />
            </Field>
            <Field id="config_table" col="CONFIG_TABLE" label="Config table" error={err("target.config_table")} help="The pipeline's own metadata table, if it keeps one">
              <Input id="config_table" value={target.config_table} onChange={(e) => setTarget({ ...target, config_table: e.target.value })} />
            </Field>
            <div className="md:col-span-2">
              <Toggle id="iceberg" checked={target.iceberg} onChange={(v) => setTarget({ ...target, iceberg: v })} label="Bronze is an Iceberg table"
                      help="Turn on to give the external volume and base location" />
            </div>
            {target.iceberg && (
              <>
                <Field id="external_volume" col="EXTERNAL_VOLUME" label="External volume" required error={err("target.external_volume")}>
                  <Input id="external_volume" value={target.external_volume} placeholder="ICEBERG_VOL" onChange={(e) => setTarget({ ...target, external_volume: e.target.value })} />
                </Field>
                <Field id="base_location" col="BASE_LOCATION" label="Base location" error={err("target.base_location")}>
                  <Input id="base_location" value={target.base_location} placeholder="bronze/crm/" onChange={(e) => setTarget({ ...target, base_location: e.target.value })} />
                </Field>
              </>
            )}
          </div>
        </WizardSection>

        <WizardSection id="load" errors={count("load")}>
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="load_type" col="LOAD_TYPE" label="Landing load" required error={err("load.load_type")}>
              <Select id="load_type" value={load.load_type} onChange={(e) => setLoad({ ...load, load_type: e.target.value })}>
                {LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}
              </Select>
            </Field>
            <Field id="bronze_load_type" col="BRONZE_LOAD_TYPE" label="Bronze load" required error={err("load.bronze_load_type")} help="SCD2 and MERGE need primary keys on every table">
              <Select id="bronze_load_type" value={load.bronze_load_type} onChange={(e) => setLoad({ ...load, bronze_load_type: e.target.value })}>
                {BRONZE_LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}
              </Select>
            </Field>
            <Field id="load_frequency" col="LOAD_FREQUENCY" label="Frequency" required error={err("load.load_frequency")}>
              <Select id="load_frequency" value={load.load_frequency} onChange={(e) => setLoad({ ...load, load_frequency: e.target.value })}>
                {FREQUENCIES.map((t) => <option key={t} value={t}>{t.replace("_", " ").toLowerCase()}</option>)}
              </Select>
            </Field>
            {load.load_frequency !== "ON_DEMAND" && (
              <Field id="job_trigger_time" col="JOB_TRIGGER_TIME" label="Trigger (cron)" required error={err("load.job_trigger_time")} help="Minute hour day month weekday, optional time zone">
                <Input id="job_trigger_time" className="font-mono text-xs" value={load.job_trigger_time} onChange={(e) => setLoad({ ...load, job_trigger_time: e.target.value })} />
              </Field>
            )}
            <Toggle id="landing_to_bronze" checked={load.landing_to_bronze_flag} onChange={(v) => setLoad({ ...load, landing_to_bronze_flag: v })}
                    label="Load bronze after landing" col="LANDING_TO_BRONZE_FLAG" help="The child pipeline moves each landed batch into bronze" />
            <Field id="last_run_timestamp" col="LAST_RUN_TIMESTAMP" label="Last run time" error={err("load.last_run_timestamp")}
                   help="Only when this source was already loaded elsewhere; SnapLogic updates it after each run">
              <Input id="last_run_timestamp" type="datetime-local" value={load.last_run_timestamp} onChange={(e) => setLoad({ ...load, last_run_timestamp: e.target.value })} />
            </Field>
            <Toggle id="completion_flag" checked={load.completion_flag} onChange={(v) => setLoad({ ...load, completion_flag: v })} col="COMPLETION_FLAG"
                    label="Last run completed" help="Starting value; SnapLogic sets it after each run" />
            <Toggle id="is_active" checked={load.is_active} onChange={(v) => setLoad({ ...load, is_active: v })}
                    label="Active" col="IS_ACTIVE" help="SnapLogic only loads active rows; switch off to register now and start later" />
          </div>
        </WizardSection>

        <WizardSection id="snaplogic" errors={count("snaplogic")}>
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="snaplogic_project_name" col="SNAPLOGIC_PROJECT_NAME" label="SnapLogic project" required error={err("snaplogic.snaplogic_project_name")}>
              <Input id="snaplogic_project_name" value={snap.snaplogic_project_name} placeholder="GDP/Ingestion" onChange={(e) => setSnap({ ...snap, snaplogic_project_name: e.target.value })} />
            </Field>
            <Field id="parent_pipeline_name" col="PARENT_PIPELINE_NAME" label="Parent pipeline" required error={err("snaplogic.parent_pipeline_name")}>
              <Input id="parent_pipeline_name" value={snap.parent_pipeline_name} placeholder="P_INGEST_MASTER" onChange={(e) => setSnap({ ...snap, parent_pipeline_name: e.target.value })} />
            </Field>
            <Field id="child_pipeline_name" col="CHILD_PIPELINE_NAME" label="Child pipeline" required error={err("snaplogic.child_pipeline_name")}>
              <Input id="child_pipeline_name" value={snap.child_pipeline_name} placeholder="C_ORACLE_TO_LANDING" onChange={(e) => { setChildTyped(true); setSnap({ ...snap, child_pipeline_name: e.target.value }); }} />
            </Field>
            <Field id="comments" col="COMMENTS" label="Comments">
              <Textarea id="comments" rows={2} value={comments} onChange={(e) => setComments(e.target.value)} />
            </Field>
          </div>
        </WizardSection>

        {checked && (
          <WizardSection id="review" errors={0}>
          <div className="space-y-3" data-testid="ingestion-review">
            <p className="text-sm">
              <span className="font-medium">{source.source_name.toUpperCase()}</span> ({source.source_type}) loads {preview.length} table{preview.length === 1 ? "" : "s"} into{" "}
              <span className="font-mono">{target.landing_database.toUpperCase()}.{target.landing_schema.toUpperCase()}</span>, then bronze{" "}
              <span className="font-mono">{target.bronze_schema.toUpperCase()}</span>; {load.load_type.toLowerCase()} to landing, {load.bronze_load_type.toLowerCase()} to bronze,{" "}
              {load.load_frequency === "ON_DEMAND" ? "on demand" : <span className="font-mono">{load.job_trigger_time}</span>}; {snap.parent_pipeline_name} → {snap.child_pipeline_name}.
              {!load.is_active && " Saved inactive."}
            </p>
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-left text-xs">
                <thead className="bg-muted/40 text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr><th className="px-3 py-2">Source</th><th className="px-3 py-2">Landing</th><th className="px-3 py-2">Bronze</th>
                      <th className="px-3 py-2">Delta key</th><th className="px-3 py-2">Primary keys</th><th className="px-3 py-2">Deletes</th></tr>
                </thead>
                <tbody className="divide-y">
                  {preview.map((r, i) => (
                    <tr key={i}>
                      <td className="max-w-[260px] truncate px-3 py-2 font-mono" title={String(r.source_table_sql)}>{String(r.source_table_sql)}</td>
                      <td className="px-3 py-2 font-mono">{String(r.landing_table)}</td>
                      <td className="px-3 py-2 font-mono">{String(r.bronze_table)}</td>
                      <td className="px-3 py-2">{String(r.source_delta_key ?? "-")}</td>
                      <td className="px-3 py-2">{String(r.source_primary_keys ?? "-")}</td>
                      <td className="px-3 py-2">{r.source_delete_action === "REQUIRED" ? `${r.source_delete_key}${r.source_delete_key_value ? ` = ${r.source_delete_key_value}` : ""}` : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sync && snapRows && <SnapLogicRows preview={snapRows} />}
            <div className="rounded-xl border p-3" data-testid="assigned-on-save">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Set when you save</p>
              <dl className="grid gap-x-4 gap-y-1 text-xs [overflow-wrap:anywhere] sm:grid-cols-[minmax(0,200px)_minmax(0,1fr)]">
                <dt className="font-mono text-muted-foreground">MASTER_ID</dt><dd>next value of SEQ_INGESTION_MASTER_ID, shared by every table below</dd>
                <dt className="font-mono text-muted-foreground">CHILD_ID</dt><dd>next value of SEQ_INGESTION_CHILD_ID, one per table ({preview.length})</dd>
                <dt className="font-mono text-muted-foreground">CREATED_BY</dt><dd>{user ?? "your Snowflake user"}</dd>
                <dt className="font-mono text-muted-foreground">CREATED_AT, UPDATED_AT</dt><dd>the time of saving</dd>
                <dt className="font-mono text-muted-foreground">LAST_RUN_TIMESTAMP, COMPLETION_FLAG</dt>
                <dd>the starting values above; SnapLogic updates them after each run</dd>
                <dt className="font-mono text-muted-foreground">TARGET_PREVIOUS_LOAD_TIME, API_URL, API_KEY{files ? "" : ", SOURCE_FILE_TYPE"}</dt>
                <dd>empty: not used for a {files ? "file" : "relational database"} source</dd>
              </dl>
            </div>
          </div>
          </WizardSection>
        )}
      </div>
      {/* the end of the form: in the page flow, so it never covers a field; room on the right for the Copilot button */}
      <div className="flex flex-wrap items-center gap-3 border-t bg-muted/20 px-5 py-4">
        <p className="w-full text-xs text-muted-foreground xl:w-auto xl:flex-1" data-testid="ingestion-status">
          {fixCount ? <span className="text-destructive">{fixCount} field{fixCount === 1 ? "" : "s"} to fix, marked in red</span>
            : checked ? "Checked: the review above shows what will be saved"
              : "Defaults are filled in. Complete the starred fields, then check."}
        </p>
        <div className="flex gap-2 xl:mr-20">
          <Button variant="outline" disabled={busy} onClick={check} data-testid="check-ingestion-end">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListChecks className="h-4 w-4" />} Check
          </Button>
          <Button disabled={busy} onClick={save} data-testid="save-ingestion-end">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save configuration
          </Button>
        </div>
      </div>
    </div>
  );
}
