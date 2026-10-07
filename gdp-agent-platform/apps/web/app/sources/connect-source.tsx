"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import {
  ArrowLeft, ArrowRight, CheckCircle2, Cloud, Database, FileSpreadsheet, Globe, HardDrive, Info, Loader2, Server,
  Snowflake, Upload, Workflow, X, XCircle,
} from "lucide-react";
import type { Connector, ExternalFile, LandResult } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { OraclePanel } from "./oracle-panel";
import {
  landExternalFiles, listExternalFiles, loadConnectors, registerExternalSource, uploadExternalFiles,
} from "./actions";

export type ManagedSource = { id: string; name: string; connector: string; database: string; schema: string };

const ICONS: Record<string, typeof Database> = {
  s3: Cloud, azure: Cloud, gcs: Cloud, upload: FileSpreadsheet, postgres: Database, sqlserver: Server,
  oracle: HardDrive, mysql: Database, salesforce: Workflow, rest_api: Globe,
};
const FIELD_LABELS: Record<string, { label: string; placeholder: string; hint?: string }> = {
  url: { label: "Location URL", placeholder: "s3://bucket/path/" },
  storage_integration: { label: "Storage integration", placeholder: "S3_CRM_INT",
    hint: "Created once by an admin; holds the cloud credentials. Nothing secret is entered here." },
  file_format: { label: "File format", placeholder: "CSV" },
  pattern: { label: "File pattern (optional)", placeholder: ".*orders.*[.]csv" },
  host: { label: "Host", placeholder: "db.internal.example.com" },
  port: { label: "Port", placeholder: "5432" },
  database: { label: "Database", placeholder: "sales" },
  schema: { label: "Schema", placeholder: "public" },
  secret: { label: "Snowflake secret", placeholder: "CRM_DB_SECRET",
    hint: "Name of a Snowflake SECRET holding the credentials. Never paste a password here." },
  instance_url: { label: "Instance URL", placeholder: "https://acme.my.salesforce.com" },
  base_url: { label: "Base URL", placeholder: "https://api.example.com/v1" },
};

type Step = "choose" | "connector" | "form" | "manage";

function Tile({ icon: Icon, title, body, tag, onClick }: {
  icon: typeof Database; title: string; body: string; tag?: string; onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick}
            className="flex flex-1 items-start gap-3 rounded-xl border p-4 text-left transition-all hover:border-primary hover:bg-primary/5">
      <span className="rounded-lg bg-primary/10 p-2 text-primary"><Icon className="h-5 w-5" /></span>
      <span>
        <span className="flex items-center gap-2 text-sm font-semibold">{title}{tag && <Badge variant="outline" className="text-[10px]">{tag}</Badge>}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{body}</span>
      </span>
    </button>
  );
}

export function ConnectSource({ initial, onClose, onSnowflake, onOpenSchema }: {
  initial?: ManagedSource | null;
  onClose: () => void;
  onSnowflake: () => void;
  onOpenSchema: (target: { database: string; schema: string }) => void;
}) {
  const [step, setStep] = useState<Step>(initial ? "manage" : "choose");
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connector, setConnector] = useState<Connector | null>(null);
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [managed, setManaged] = useState<ManagedSource | null>(initial ?? null);
  const [guidance, setGuidance] = useState<string | null>(null);
  const [files, setFiles] = useState<ExternalFile[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [table, setTable] = useState("");
  const [landed, setLanded] = useState<LandResult | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadConnectors().then((r) => r.ok && setConnectors(r.data.connectors));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const refreshFiles = (id: string) => start(async () => {
    const r = await listExternalFiles(id);
    if (!r.ok) { setError(r.error); return; }
    setFiles(r.data.files);
    setChosen(r.data.files.map((f) => f.path));
  });

  useEffect(() => {
    if (!managed) return;
    const spec = connectors.find((c) => c.id === managed.connector);
    if (spec && !spec.landable) { setGuidance(spec.guidance); return; }
    if (spec) refreshFiles(managed.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [managed?.id, connectors.length]);

  const managedSpec = managed ? connectors.find((c) => c.id === managed.connector) : null;

  const register = () => start(async () => {
    if (!connector) return;
    setError("");
    const r = await registerExternalSource({ source_system_name: name, connector: connector.id, config });
    if (!r.ok) { setError(r.error); return; }
    setGuidance(r.data.guidance);
    setManaged({ id: r.data.source_system_id, name: r.data.source_system_name, connector: connector.id,
                 database: r.data.landing.database, schema: r.data.landing.schema });
    setStep("manage");
  });

  const upload = () => start(async () => {
    if (!managed || !fileInput.current?.files?.length) return;
    setError("");
    const form = new FormData();
    Array.from(fileInput.current.files).forEach((f) => form.append("files", f));
    const r = await uploadExternalFiles(managed.id, form);
    if (!r.ok) { setError(r.error); return; }
    fileInput.current.value = "";
    const listed = await listExternalFiles(managed.id);
    if (listed.ok) { setFiles(listed.data.files); setChosen(listed.data.files.map((f) => f.path)); }
  });

  const land = () => start(async () => {
    if (!managed) return;
    setError("");
    const r = await landExternalFiles(managed.id, chosen, table || undefined);
    if (!r.ok) { setError(r.error); return; }
    setLanded(r.data);
  });

  const fileConnectors = connectors.filter((c) => c.kind === "FILE");
  const extractedConnectors = connectors.filter((c) => c.kind !== "FILE" && c.extractor);
  const otherConnectors = connectors.filter((c) => c.kind !== "FILE" && !c.extractor);
  const isOracle = connector?.id === "oracle";
  const oracleReady = !isOracle || (!!config.host && !!config.user && !!(config.service_name || config.sid)
    && ((config.runtime ?? "snowflake") === "snowflake" || !!config.password_env));
  const canRegister = connector && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name) && oracleReady
    && connector.fields.filter((f) => ["url", "storage_integration"].includes(f)).every((f) => config[f]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 p-6">
      <div role="dialog" aria-label="Connect source" className="relative w-full max-w-3xl rounded-2xl border bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-6 py-4">
          {step !== "choose" && step !== "manage" && (
            <button type="button" aria-label="Back" className="rounded-md p-1 hover:bg-muted"
                    onClick={() => setStep(step === "form" ? "connector" : "choose")}>
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <div>
            <h3 className="text-base font-semibold">
              {step === "manage" && managed ? `${managed.name}: ${managed.connector === "oracle" ? "Oracle source" : "land into Snowflake"}` : "Connect a source"}
            </h3>
            <p className="text-xs text-muted-foreground">
              {step === "choose" && "Where does the data live?"}
              {step === "connector" && "External systems land into Snowflake first, then are profiled and modeled like any table."}
              {step === "form" && connector?.label}
              {step === "manage" && managed && <span className="font-mono">{managed.database}.{managed.schema}</span>}
            </p>
          </div>
          <button type="button" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-4 px-6 py-5">
          {step === "choose" && (
            <div className="flex flex-col gap-3 sm:flex-row">
              <Tile icon={Snowflake} title="Snowflake database or share"
                    body="Already in Snowflake. Browse it directly; profiling reads tables in place and copies nothing."
                    onClick={() => { onSnowflake(); onClose(); }} />
              <Tile icon={Globe} title="External system" tag="lands first"
                    body="Files in cloud storage or uploaded, an operational database, a SaaS app or an API."
                    onClick={() => setStep("connector")} />
            </div>
          )}

          {step === "connector" && (
            <div className="space-y-4">
              {[["Files (landed by the platform)", fileConnectors],
                ["Databases extracted and landed by the platform", extractedConnectors],
                ["Databases, SaaS and APIs (landed by a Snowflake connector)", otherConnectors]].filter(([, list]) => (list as Connector[]).length).map(
                ([title, list]) => (
                  <div key={title as string}>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{title as string}</p>
                    <div className="grid gap-2 sm:grid-cols-3">
                      {(list as Connector[]).map((c) => {
                        const Icon = ICONS[c.id] ?? Database;
                        return (
                          <button key={c.id} type="button"
                                  onClick={() => { setConnector(c); setConfig(c.kind === "FILE" ? { file_format: "CSV" } : c.id === "oracle" ? { port: "1521", runtime: "snowflake", service_name: "" } : {}); setStep("form"); }}
                                  className="flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm hover:border-primary hover:bg-primary/5">
                            <Icon className="h-4 w-4 text-muted-foreground" /> {c.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ),
              )}
            </div>
          )}

          {step === "form" && connector && (
            <div className="space-y-3">
              {!connector.landable && connector.guidance && (
                <p className="flex gap-2 rounded-lg border border-warning/40 bg-warning/5 p-3 text-xs">
                  <Info className="h-4 w-4 shrink-0 text-warning" /> {connector.guidance}
                </p>
              )}
              <div>
                <label htmlFor="ext_name" className="mb-1 block text-xs font-medium">Source name</label>
                <Input id="ext_name" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="CRM_FILES" maxLength={64} />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Tables land in <span className="font-mono">EXT_{name || "NAME"}</span> of the platform database.
                </p>
              </div>
              {isOracle ? <OracleForm config={config} setConfig={setConfig} /> : (
              <div className="grid gap-3 sm:grid-cols-2">
                {connector.fields.map((f) => {
                  const meta = FIELD_LABELS[f] ?? { label: f, placeholder: "" };
                  return (
                    <div key={f} className={cn(["url", "storage_integration", "secret"].includes(f) && "sm:col-span-2")}>
                      <label htmlFor={`ext_${f}`} className="mb-1 block text-xs font-medium">{meta.label}</label>
                      {f === "file_format" ? (
                        <select id={`ext_${f}`} value={config[f] ?? "CSV"} onChange={(e) => setConfig({ ...config, [f]: e.target.value })}
                                className="h-9 w-full rounded-md border bg-card px-2 text-sm">
                          {["CSV", "PARQUET", "JSON"].map((v) => <option key={v} value={v}>{v}</option>)}
                        </select>
                      ) : (
                        <Input id={`ext_${f}`} value={config[f] ?? ""} placeholder={meta.placeholder}
                               onChange={(e) => setConfig({ ...config, [f]: e.target.value })} />
                      )}
                      {meta.hint && <p className="mt-1 text-[11px] text-muted-foreground">{meta.hint}</p>}
                    </div>
                  );
                })}
              </div>
              )}
            </div>
          )}

          {step === "manage" && managed && (
            <div className="space-y-4">
              {guidance && (
                <div className="flex gap-2 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm">
                  <Info className="h-4 w-4 shrink-0 text-warning" />
                  <div>
                    <p className="font-medium">Registered. Landing happens outside the platform for this connector.</p>
                    <p className="text-xs text-muted-foreground">{guidance} Target schema: <span className="font-mono">{managed.database}.{managed.schema}</span>.</p>
                  </div>
                </div>
              )}
              {managed.connector === "oracle" && (
                <OraclePanel sourceId={managed.id} name={managed.name}
                             onOpenSchema={(target) => { onOpenSchema(target); onClose(); }} />
              )}
              {managedSpec?.landable && !managedSpec.extractor && (
                <>
                  {managed.connector === "upload" && (
                    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed p-3">
                      <Upload className="h-4 w-4 text-muted-foreground" />
                      <input ref={fileInput} type="file" multiple accept=".csv,.tsv,.txt,.parquet,.json,.ndjson,.gz"
                             aria-label="Files to upload" className="text-sm" />
                      <Button size="sm" variant="outline" disabled={pending} onClick={upload}>Upload to stage</Button>
                    </div>
                  )}
                  <div>
                    <div className="mb-2 flex items-center gap-2">
                      <p className="text-sm font-medium">Files on the stage</p>
                      <span className="text-xs text-muted-foreground">{chosen.length} of {files.length} selected</span>
                      <Button size="sm" variant="ghost" className="ml-auto" disabled={pending} onClick={() => refreshFiles(managed.id)}>Refresh</Button>
                    </div>
                    <div className="max-h-56 overflow-y-auto rounded-lg border">
                      {files.length === 0 && (
                        <p className="p-3 text-sm text-muted-foreground">
                          {pending ? "Listing files…" : managed.connector === "upload" ? "Upload files to begin." : "No files found at the location yet."}
                        </p>
                      )}
                      {files.map((f) => (
                        <label key={f.path} className="flex items-center gap-3 border-b px-3 py-1.5 text-sm last:border-0">
                          <input type="checkbox" checked={chosen.includes(f.path)}
                                 onChange={() => setChosen((p) => (p.includes(f.path) ? p.filter((x) => x !== f.path) : [...p, f.path]))} />
                          <span className="truncate font-mono text-xs">{f.path}</span>
                          <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">
                            {f.size != null ? `${Math.max(1, Math.round(Number(f.size) / 1024))} KB` : ""}
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-end gap-3">
                    <div>
                      <label htmlFor="land_table" className="mb-1 block text-xs font-medium">Load into one table (optional)</label>
                      <Input id="land_table" value={table} onChange={(e) => setTable(e.target.value.toUpperCase())}
                             placeholder="One table per file" className="w-56" />
                    </div>
                    <Button disabled={pending || chosen.length === 0} onClick={land}>
                      {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
                      Land {chosen.length} file{chosen.length === 1 ? "" : "s"} into Snowflake
                    </Button>
                  </div>
                </>
              )}
              {landed && (
                <div className="space-y-2 rounded-lg border p-3">
                  <p className="text-sm font-medium">Landing results</p>
                  {landed.tables.map((t) => (
                    <p key={t.table} className="flex items-center gap-2 text-sm">
                      {t.status === "LOADED" ? <CheckCircle2 className="h-4 w-4 text-success" /> : <XCircle className="h-4 w-4 text-destructive" />}
                      <span className="font-mono">{t.table}</span>
                      <span className="text-xs text-muted-foreground">
                        {t.status === "LOADED" ? `${t.rows_loaded.toLocaleString()} rows from ${t.files.length} file(s)` : t.error}
                      </span>
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}

          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>

        <footer className="flex items-center gap-3 border-t px-6 py-3">
          {step === "form" && (
            <>
              <p className="text-[11px] text-muted-foreground">
                {isOracle ? "No password is stored here: it goes into a Snowflake secret (next step) or stays in an environment variable on the API host."
                  : "Only object names are stored; credentials stay in Snowflake."}
              </p>
              <Button className="ml-auto" disabled={!canRegister || pending} onClick={register}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Register source
              </Button>
            </>
          )}
          {step === "manage" && managed && (
            <Button className="ml-auto" variant={landed ? "default" : "outline"}
                    onClick={() => { onOpenSchema({ database: managed.database, schema: managed.schema }); onClose(); }}>
              Open {managed.schema} to profile <ArrowRight className="h-4 w-4" />
            </Button>
          )}
          {(step === "choose" || step === "connector") && (
            <Button variant="ghost" className="ml-auto" onClick={onClose}>Cancel</Button>
          )}
        </footer>
      </div>
    </div>
  );
}


/** Oracle connection settings: where the database is, how to reach it, and which runtime reads it. */
function OracleForm({ config, setConfig }: {
  config: Record<string, string>; setConfig: (c: Record<string, string>) => void;
}) {
  const bySid = config.sid !== undefined && config.service_name === undefined;
  const set = (k: string, v: string) => setConfig({ ...config, [k]: v });
  const connectBy = (k: "service_name" | "sid") => {
    const rest = Object.fromEntries(Object.entries(config).filter(([key]) => key !== "service_name" && key !== "sid"));
    setConfig({ ...rest, [k]: "" });
  };
  const runtime = config.runtime ?? "snowflake";
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_120px]">
        <div>
          <label htmlFor="ora_host" className="mb-1 block text-xs font-medium">Host</label>
          <Input id="ora_host" value={config.host ?? ""} onChange={(e) => set("host", e.target.value.trim())} placeholder="db.acme.com or 10.0.4.12" />
        </div>
        <div>
          <label htmlFor="ora_port" className="mb-1 block text-xs font-medium">Port</label>
          <Input id="ora_port" value={config.port ?? "1521"} onChange={(e) => set("port", e.target.value.replace(/[^0-9]/g, ""))} />
        </div>
      </div>
      <div>
        <div className="mb-1 flex items-center gap-2 text-xs font-medium">
          Connect by
          {(["service_name", "sid"] as const).map((k) => {
            const active = k === "sid" ? bySid : !bySid;
            return (
              <button key={k} type="button" aria-pressed={active} onClick={() => connectBy(k)}
                      className={cn("rounded-md border px-2 py-0.5", active ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                {k === "sid" ? "SID" : "Service name"}
              </button>
            );
          })}
        </div>
        <Input aria-label={bySid ? "SID" : "Service name"} value={(bySid ? config.sid : config.service_name) ?? ""}
               onChange={(e) => set(bySid ? "sid" : "service_name", e.target.value.trim())} placeholder={bySid ? "ORCL" : "ORCLPDB1"} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="ora_user" className="mb-1 block text-xs font-medium">User</label>
          <Input id="ora_user" value={config.user ?? ""} onChange={(e) => set("user", e.target.value.toUpperCase())} placeholder="ETL_READER" />
        </div>
        <div>
          <label htmlFor="ora_owner" className="mb-1 block text-xs font-medium">Schema owner</label>
          <Input id="ora_owner" value={config.schema_owner ?? ""} onChange={(e) => set("schema_owner", e.target.value.toUpperCase())}
                 placeholder={config.user || "HR"} />
          <p className="mt-1 text-[11px] text-muted-foreground">Whose tables to read; defaults to the user.</p>
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium">Where the extraction runs</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {([["snowflake", "Inside Snowflake", "Recommended. Oracle must be reachable from Snowflake (public endpoint, allow-listed IPs or PrivateLink). The password is kept in a Snowflake secret."],
             ["api_host", "On this platform's server", "For an Oracle only your network reaches. The password is read from an environment variable on the API host."]] as const).map(([v, t, d]) => (
            <button key={v} type="button" aria-pressed={runtime === v} onClick={() => set("runtime", v)}
                    className={cn("rounded-xl border p-3 text-left", runtime === v ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:bg-muted/40")}>
              <span className="block text-sm font-semibold">{t}</span>
              <span className="block text-[11px] text-muted-foreground">{d}</span>
            </button>
          ))}
        </div>
      </div>
      {runtime === "api_host" && (
        <div>
          <label htmlFor="ora_env" className="mb-1 block text-xs font-medium">Password environment variable</label>
          <Input id="ora_env" value={config.password_env ?? ""} onChange={(e) => set("password_env", e.target.value.toUpperCase())}
                 placeholder="ORACLE_HR_PASSWORD" className="font-mono" />
          <p className="mt-1 text-[11px] text-muted-foreground">Set it on the machine that runs the API, then restart the API.</p>
        </div>
      )}
    </div>
  );
}
