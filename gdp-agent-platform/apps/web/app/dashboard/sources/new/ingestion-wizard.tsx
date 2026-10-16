"use client";

import Link from "next/link";
import { useState, useTransition, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, Copy, Loader2, Plus, Save, Trash2 } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { explain } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { saveIngestion, validateIngestion, type IngestionPayload } from "../actions";
import type { DomainOption } from "./wizard";

const SOURCE_TYPES = ["ORACLE", "SQLSERVER", "POSTGRES", "MYSQL", "SALESFORCE", "REST_API", "S3", "SFTP", "OTHER"];
const FILE_TYPES = ["TABLE", "CSV", "JSON", "PARQUET", "XLSX"];
const LOAD_TYPES = ["APPEND", "TRUNCATE", "SCD2"];
const BRONZE_LOAD_TYPES = ["TRUNCATE", "SCD2", "MERGE"];
const FREQUENCIES = ["HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND"];

type Table = {
  source_table_sql: string; landing_table: string; bronze_table: string; source_delta_key: string; source_primary_keys: string;
  source_where_clause: string; source_audit_columns: string; deletes: boolean; source_delete_key: string;
  source_delete_key_value: string; source_delete_query: string; target_previous_load_time: string;
};
const EMPTY_TABLE: Table = {
  source_table_sql: "", landing_table: "", bronze_table: "", source_delta_key: "", source_primary_keys: "", source_where_clause: "",
  source_audit_columns: "", deletes: false, source_delete_key: "", source_delete_key_value: "", source_delete_query: "",
  target_previous_load_time: "",
};

const STEPS = [
  { id: "source", title: "Source", hint: "The system and where it is read from" },
  { id: "tables", title: "Tables", hint: "What to read and how to track changes" },
  { id: "target", title: "Target", hint: "Landing and bronze in Snowflake" },
  { id: "load", title: "Load", hint: "Strategy and schedule" },
  { id: "snaplogic", title: "SnapLogic", hint: "Project, pipelines and API" },
  { id: "review", title: "Review", hint: "Check and save" },
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

/** Onboard an external source for SnapLogic: six steps, each checked by the API before moving on; saving writes one
 * catalog row for the source and one control row per table (CORE.INGESTION_CONTROL_CONFIG) under one
 * master ID. SnapLogic reads the table; nothing is sent to it. */
export function IngestionWizard({ domains, user }: { domains: DomainOption[]; user: string | null }) {
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [source, setSource] = useState({ source_name: "", source_type: "ORACLE", domain_id: "", source_account: "",
                                         source_database_path: "", source_schema_file: "", source_file_type: "TABLE", source_sheet_name: "" });
  const [tables, setTables] = useState<Table[]>([{ ...EMPTY_TABLE }]);
  const [target, setTarget] = useState({ target_account: "", landing_database: "", landing_schema: "LANDING", bronze_schema: "BRONZE",
                                         external_stage: "", iceberg: false, external_volume: "", base_location: "", config_table: "" });
  const [load, setLoad] = useState({ load_type: "APPEND", bronze_load_type: "TRUNCATE", load_frequency: "DAILY",
                                     job_trigger_time: "0 2 * * * UTC", landing_to_bronze_flag: true, is_active: true,
                                     last_run_timestamp: "", completion_flag: false });
  const [snap, setSnap] = useState({ snaplogic_project_name: "", parent_pipeline_name: "", child_pipeline_name: "", api_url: "", api_key: "" });
  const [comments, setComments] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Record<string, unknown>[]>([]);
  const [saved, setSaved] = useState<{ master_id: number; tables: number; parent: string } | null>(null);
  const [busy, start] = useTransition();

  const payload = (): IngestionPayload => ({
    source, target, load, snaplogic: snap, comments,
    tables: tables.map((t) => ({ ...t, source_delete_action: t.deletes ? "REQUIRED" : "NOT_REQUIRED" })),
  });
  const err = (path: string) => errors[path];
  const stepId = STEPS[step].id;
  const scoped = (all: Record<string, string>, id: string) =>
    Object.fromEntries(Object.entries(all).filter(([k]) => k === id || k.startsWith(`${id}.`)));

  const next = () => start(async () => {
    const r = await validateIngestion(payload());
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    const mine = stepId === "review" ? r.data.errors : scoped(r.data.errors, stepId);
    setErrors(mine);
    if (Object.keys(mine).length) {
      toast("error", `Check ${Object.keys(mine).length} field${Object.keys(mine).length === 1 ? "" : "s"} on this step`);
      return;
    }
    setPreview(r.data.rows);
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  });
  const save = () => start(async () => {
    const r = await saveIngestion(payload());
    if (!r.ok) {
      const e = explain(r.error);
      setErrors(e.fields);
      const first = Object.keys(e.fields)[0];
      if (first) setStep(Math.max(0, STEPS.findIndex((s) => first === s.id || first.startsWith(`${s.id}.`))));
      toast("error", e.message);
      return;
    }
    if ("pending_approval" in r.data) { toast("info", r.data.detail); return; }
    const d = r.data;
    toast("ok", `Configuration saved: master ID ${d.master_id}, ${d.children.length} table${d.children.length === 1 ? "" : "s"} registered for ${d.parent_pipeline_name}.`);
    setSaved({ master_id: d.master_id, tables: d.children.length, parent: d.parent_pipeline_name });
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
        <div className="flex gap-2">
          <Link href="/dashboard/sources" className={buttonVariants()}>Open in the Source Store</Link>
          <Button variant="outline" onClick={() => { setSaved(null); setStep(0); setTables([{ ...EMPTY_TABLE }]); setPreview([]); }}>Onboard another</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="surface overflow-hidden" data-testid="ingestion-wizard">
      <ol className="flex flex-wrap gap-1 border-b bg-muted/30 p-2" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s.id}>
            <button type="button" disabled={i > step} onClick={() => setStep(i)} aria-current={i === step ? "step" : undefined}
                    className={cn("flex items-center gap-2 rounded-lg px-3 py-1.5 text-left text-xs transition",
                      i === step ? "bg-card font-medium shadow-sm ring-1 ring-border" : i < step ? "text-foreground hover:bg-card/60" : "text-muted-foreground")}>
              <span className={cn("grid h-5 w-5 place-items-center rounded-full text-[10px] font-semibold",
                i < step ? "bg-success text-white" : i === step ? "bg-primary text-primary-foreground" : "bg-muted")}>
                {i < step ? "✓" : i + 1}
              </span>
              {s.title}
            </button>
          </li>
        ))}
      </ol>
      <div className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold">{STEPS[step].title}</h3>
          <p className="text-xs text-muted-foreground">{STEPS[step].hint}</p>
        </div>

        {stepId === "source" && (
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="source_name" label="Source name" required error={err("source.source_name")} help="Letters, digits and _; names the landing tables">
              <Input id="source_name" value={source.source_name} placeholder="CRM_ORACLE" onChange={(e) => setSource({ ...source, source_name: e.target.value })} />
            </Field>
            <Field id="source_type" label="Source type" error={err("source.source_type")}>
              <Select id="source_type" value={source.source_type} onChange={(e) => setSource({ ...source, source_type: e.target.value })}>
                {SOURCE_TYPES.map((t) => <option key={t}>{t}</option>)}
              </Select>
            </Field>
            <Field id="source_account" col="SOURCE_ACCOUNT" label="Source account" error={err("source.source_account")} help="The SnapLogic account that connects to the source">
              <Input id="source_account" value={source.source_account} placeholder="crm_oracle_prod" onChange={(e) => setSource({ ...source, source_account: e.target.value })} />
            </Field>
            <Field id="domain" col="DOMAIN_ID" label="Domain">
              <Select id="domain" value={source.domain_id} onChange={(e) => setSource({ ...source, domain_id: e.target.value })}>
                <option value="">No domain yet</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select>
            </Field>
            <Field id="source_database_path" col="SOURCE_DATABASE_PATH" label="Database or path" error={err("source.source_database_path")} help="Database, service name or folder path">
              <Input id="source_database_path" value={source.source_database_path} placeholder="CRMPROD or /exports/crm" onChange={(e) => setSource({ ...source, source_database_path: e.target.value })} />
            </Field>
            <Field id="source_schema_file" col="SOURCE_SCHEMA_FILE" label="Schema or file" error={err("source.source_schema_file")}>
              <Input id="source_schema_file" value={source.source_schema_file} placeholder="SALES or customers_*.csv" onChange={(e) => setSource({ ...source, source_schema_file: e.target.value })} />
            </Field>
            <Field id="source_file_type" col="SOURCE_FILE_TYPE" label="Source format" error={err("source.source_file_type")}>
              <Select id="source_file_type" value={source.source_file_type} onChange={(e) => setSource({ ...source, source_file_type: e.target.value })}>
                {FILE_TYPES.map((t) => <option key={t}>{t}</option>)}
              </Select>
            </Field>
            {source.source_file_type === "XLSX" && (
              <Field id="source_sheet_name" col="SOURCE_SHEET_NAME" label="Sheet name" required error={err("source.source_sheet_name")}>
                <Input id="source_sheet_name" value={source.source_sheet_name} placeholder="Customers" onChange={(e) => setSource({ ...source, source_sheet_name: e.target.value })} />
              </Field>
            )}
          </div>
        )}

        {stepId === "tables" && (
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
                <Field id={`sql-${i}`} col="SOURCE_TABLE_SQL" label="Source table or query" required error={err(`tables.${i}.source_table_sql`)}
                       help="A table name (SALES.CUSTOMERS) or one read-only SELECT">
                  <Textarea id={`sql-${i}`} rows={2} className="font-mono text-xs" value={t.source_table_sql} placeholder="SALES.CUSTOMERS"
                            onChange={(e) => setTable(i, { source_table_sql: e.target.value })} />
                </Field>
                <div className="grid gap-3 md:grid-cols-3">
                  <Field id={`delta-${i}`} col="SOURCE_DELTA_KEY" label="Delta key" error={err(`tables.${i}.source_delta_key`)} help="Column that marks new or changed rows">
                    <Input id={`delta-${i}`} value={t.source_delta_key} placeholder="UPDATED_AT" onChange={(e) => setTable(i, { source_delta_key: e.target.value })} />
                  </Field>
                  <Field id={`pk-${i}`} col="SOURCE_PRIMARY_KEYS" label="Primary keys" error={err(`tables.${i}.source_primary_keys`)} help="Comma-separated">
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
                <Field id={`watermark-${i}`} col="TARGET_PREVIOUS_LOAD_TIME" label="Previous load time" error={err(`tables.${i}.target_previous_load_time`)}
                       help="Where the first delta load starts (the watermark); leave empty to load everything">
                  <Input id={`watermark-${i}`} type="datetime-local" value={t.target_previous_load_time} onChange={(e) => setTable(i, { target_previous_load_time: e.target.value })} />
                </Field>
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
        )}

        {stepId === "target" && (
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
        )}

        {stepId === "load" && (
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
        )}

        {stepId === "snaplogic" && (
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="snaplogic_project_name" col="SNAPLOGIC_PROJECT_NAME" label="SnapLogic project" required error={err("snaplogic.snaplogic_project_name")}>
              <Input id="snaplogic_project_name" value={snap.snaplogic_project_name} placeholder="GDP/Ingestion" onChange={(e) => setSnap({ ...snap, snaplogic_project_name: e.target.value })} />
            </Field>
            <Field id="parent_pipeline_name" col="PARENT_PIPELINE_NAME" label="Parent pipeline" required error={err("snaplogic.parent_pipeline_name")}>
              <Input id="parent_pipeline_name" value={snap.parent_pipeline_name} placeholder="P_INGEST_MASTER" onChange={(e) => setSnap({ ...snap, parent_pipeline_name: e.target.value })} />
            </Field>
            <Field id="child_pipeline_name" col="CHILD_PIPELINE_NAME" label="Child pipeline" required error={err("snaplogic.child_pipeline_name")}>
              <Input id="child_pipeline_name" value={snap.child_pipeline_name} placeholder="C_ORACLE_TO_LANDING" onChange={(e) => setSnap({ ...snap, child_pipeline_name: e.target.value })} />
            </Field>
            <Field id="api_url" col="API_URL" label="API URL" error={err("snaplogic.api_url")} help="The pipeline's triggered task URL (https)">
              <Input id="api_url" value={snap.api_url} placeholder="https://elastic.snaplogic.com/api/1/rest/slsched/feed/..." onChange={(e) => setSnap({ ...snap, api_url: e.target.value })} />
            </Field>
            <Field id="api_key" col="API_KEY" label="Secret reference" error={err("snaplogic.api_key")}
                   help="The name of the API key's secret in your vault. Never paste the token itself.">
              <Input id="api_key" value={snap.api_key} placeholder="vault:snaplogic/ingestion-token" autoComplete="off" onChange={(e) => setSnap({ ...snap, api_key: e.target.value })} />
            </Field>
            <Field id="comments" col="COMMENTS" label="Comments">
              <Textarea id="comments" rows={2} value={comments} onChange={(e) => setComments(e.target.value)} />
            </Field>
          </div>
        )}

        {stepId === "review" && (
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
            <div className="rounded-xl border p-3" data-testid="assigned-on-save">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Set when you save</p>
              <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[200px_1fr]">
                <dt className="font-mono text-muted-foreground">MASTER_ID</dt><dd>next value of SEQ_INGESTION_MASTER_ID, shared by every table below</dd>
                <dt className="font-mono text-muted-foreground">CHILD_ID</dt><dd>next value of SEQ_INGESTION_CHILD_ID, one per table ({preview.length})</dd>
                <dt className="font-mono text-muted-foreground">CREATED_BY</dt><dd>{user ?? "your Snowflake user"}</dd>
                <dt className="font-mono text-muted-foreground">CREATED_AT, UPDATED_AT</dt><dd>the time of saving</dd>
                <dt className="font-mono text-muted-foreground">TARGET_PREVIOUS_LOAD_TIME, LAST_RUN_TIMESTAMP, COMPLETION_FLAG</dt>
                <dd>the starting values above; SnapLogic updates them after each run</dd>
              </dl>
            </div>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 border-t bg-muted/20 p-4">
        <Button variant="ghost" disabled={step === 0 || busy} onClick={() => { setErrors({}); setStep((s) => s - 1); }}><ArrowLeft className="h-4 w-4" /> Back</Button>
        <span className="flex-1 text-xs text-muted-foreground">Step {step + 1} of {STEPS.length}</span>
        {stepId === "review" ? (
          <Button disabled={busy} onClick={save} data-testid="save-ingestion">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save configuration
          </Button>
        ) : (
          <Button disabled={busy} onClick={next} data-testid="wizard-next">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Next <ArrowRight className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
