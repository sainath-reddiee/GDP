"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, CheckCircle2, Loader2, Search, XCircle } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, OnboardResult, ShareList, ShareTable, SnapLogicPreview, SnapLogicStatus } from "@/lib/hub-types";
import { SnapLogicRows } from "@/components/sources/snaplogic-preview";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote, explain } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { listSchemas, listShareSchemas, mountShare, onboardShare, previewShare, retrySnaplogic, snaplogicPreviewShare, snaplogicStatus,
  type ShareSchema } from "../actions";
import type { DomainOption } from "./wizard";

const LOAD_TYPES = ["APPEND", "TRUNCATE", "SCD2"];
const BRONZE_LOAD_TYPES = ["TRUNCATE", "SCD2", "MERGE"];
const FREQUENCIES = ["HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND"];
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

/** readable: the database the share is mounted as is one this role can read (listed in INFORMATION_SCHEMA); a share
 *  can be mounted in the account while the role lacks IMPORTED PRIVILEGES on it. */
type ShareEntry = { key: string; share: string | null; provider: string | null; database: string | null; comment?: string | null;
  readable: boolean; kind: "share" | "database" };

const slug = (s: string) => s.toUpperCase().replace(/[^A-Z0-9_]/g, "_").replace(/^([^A-Z])/, "S_$1").slice(0, 64);
const landingName = (source: string, table: string) => `${source.toUpperCase()}__${table.toUpperCase().replace(/[^A-Z0-9_]/g, "_")}`;
const n = (v: number | null | undefined) => (v == null ? "-" : Number(v).toLocaleString());

/** One titled block of the form. */
function Section({ title, aside, children, testId }: { title: string; aside?: React.ReactNode; children: React.ReactNode; testId?: string }) {
  return (
    <section className="space-y-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground/70">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Onboard tables from a Snowflake database or share on one form: pick the database or share (mounting it if needed),
 * a schema and its tables, say where they land, then Load (each table unloaded once to Parquet, copied into landing)
 * and continue to profiling. Schemas and tables are listed from Snowflake as soon as the choice before them is made,
 * and changing a choice clears the ones that depended on it. */
export function ShareWizard({ shares, domains, databases, landing, initialName = "" }: {
  shares: ShareList; domains: DomainOption[]; databases: CatalogDatabase[]; landing: LandingDefaults | null; initialName?: string;
}) {
  const entries = useMemo<ShareEntry[]>(() => {
    const seen = new Set<string>();
    const readable = new Set(shares.mounted.map((d) => d.toUpperCase()));
    const out: ShareEntry[] = shares.shares.map((s) => {
      if (s.database_name) seen.add(s.database_name.toUpperCase());
      return { key: `${s.owner_account}.${s.name}`, share: s.name, provider: s.owner_account, database: s.database_name, comment: s.comment,
               readable: !!s.database_name && readable.has(s.database_name.toUpperCase()), kind: "share" };
    });
    for (const d of shares.mounted) {
      if (!seen.has(d.toUpperCase())) {
        seen.add(d.toUpperCase());
        out.push({ key: `db:${d}`, share: null, provider: null, database: d, readable: true, kind: "share" });
      }
    }
    for (const d of databases) {
      if (!seen.has(d.database_name.toUpperCase())) {
        seen.add(d.database_name.toUpperCase());
        out.push({ key: `db:${d.database_name}`, share: null, provider: null, database: d.database_name, readable: true,
                   kind: d.type === "IMPORTED DATABASE" ? "share" : "database" });
      }
    }
    return out;
  }, [shares, databases]);

  const [busy, setBusy] = useState<"" | "schemas" | "tables" | "mount" | "run" | "preview" | "retry">("");
  const [error, setError] = useState<string | null>(null);
  const [entry, setEntry] = useState<ShareEntry | null>(null);
  const [mountAs, setMountAs] = useState("");
  const [manual, setManual] = useState({ provider: "", share: "" });
  const [database, setDatabase] = useState("");
  const [schemas, setSchemas] = useState<ShareSchema[] | null>(null);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<ShareTable[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [sourceName, setSourceName] = useState(initialName);
  const [autoName, setAutoName] = useState("");
  const [runName, setRunName] = useState("");
  const [domainId, setDomainId] = useState("");
  const standard = databases.filter((d) => d.type === "STANDARD").map((d) => d.database_name);
  const platform = landing?.default.landing_database ?? standard[0] ?? "";
  const [landingDb, setLandingDb] = useState(platform);
  const [landingSchemas, setLandingSchemas] = useState<string[]>(landing?.schemas ?? []);
  const [landingSchema, setLandingSchema] = useState(landing?.default.landing_schema ?? "LANDING");
  const [result, setResult] = useState<OnboardResult | null>(null);
  // a slower answer for an earlier choice never overwrites the current one
  const latest = useRef(0);
  // SnapLogic's config tables (Admin, SNAPLOGIC_SYNC): when the sync is on, the form also asks what SnapLogic needs
  // to keep loading the share, and the rows are written after the data has landed
  const [sync, setSync] = useState<SnapLogicStatus | null>(null);
  const [snap, setSnap] = useState({ bronze_database: "", bronze_schema: "BRONZE", load_type: "APPEND", bronze_load_type: "SCD2",
                                     load_frequency: "DAILY", schedule_cron: "0 2 * * * UTC", is_active: false, source_system_desc: "",
                                     airflow_source_system: "", silver_command_name: "" });
  const [pks, setPks] = useState<Record<string, string>>({});
  const [snapRows, setSnapRows] = useState<SnapLogicPreview | null>(null);
  const [retried, setRetried] = useState<string | null>(null);
  useEffect(() => {
    void snaplogicStatus().then((r) => {
      if (!r.ok || !r.data.enabled) return;
      setSync(r.data);
      const d = r.data.defaults;
      setSnap((s) => ({ ...s, bronze_database: String(d.bronze_database ?? ""), load_type: String(d.load_type ?? s.load_type),
                        bronze_load_type: String(d.bronze_load_type ?? s.bronze_load_type), load_frequency: String(d.load_frequency ?? s.load_frequency) }));
    });
  }, []);
  const snaplogicAnswers = () => sync ? {
    ...snap, schedule_cron: snap.load_frequency === "ON_DEMAND" ? "" : snap.schedule_cron,
    primary_keys: Object.fromEntries(Object.entries(pks).filter(([, v]) => v.trim())),
  } : undefined;
  const shareBody = () => ({
    database, schema, tables: Array.from(picked), source_system_name: sourceName, run_name: runName || undefined,
    domain_id: domainId || undefined, landing_database: landingDb || undefined, landing_schema: landingSchema || undefined,
    snaplogic: snaplogicAnswers(),
  });
  const previewSnap = async () => {
    setBusy("preview");
    const r = await snaplogicPreviewShare(shareBody());
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    setSnapRows(r.data);
  };
  const retry = async (syncId: string) => {
    setBusy("retry");
    const r = await retrySnaplogic(syncId, { target: { bronze_schema: snap.bronze_schema, bronze_database: snap.bronze_database },
      load: { load_type: snap.load_type, bronze_load_type: snap.bronze_load_type, load_frequency: snap.load_frequency,
              job_trigger_time: snap.load_frequency === "ON_DEMAND" ? "" : snap.schedule_cron, is_active: snap.is_active },
      primary_keys: snaplogicAnswers()?.primary_keys ?? {} });
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    if ("pending_approval" in r.data) { setRetried(r.data.detail); return; }
    setRetried(`Ingestion configuration written: master row ${r.data.snaplogic?.master_config_id}, detail rows ${(r.data.snaplogic?.child_config_ids ?? []).join(", ")}.`);
  };

  const shownTables = useMemo(() => (tables ?? []).filter((t) => t.table_name.toLowerCase().includes(filter.toLowerCase())), [tables, filter]);

  useEffect(() => {
    if (!landingDb || landingDb === landing?.database) { setLandingSchemas(landing?.schemas ?? []); return; }
    let live = true;
    listSchemas(landingDb).then((r) => { if (live && r.ok) setLandingSchemas(r.data.schemas.map((s) => s.schema_name)); });
    return () => { live = false; };
  }, [landingDb, landing]);

  // the schemas of a database are listed as soon as it is chosen
  const openSchemas = async (db: string, token: number) => {
    setBusy("schemas");
    const r = await listShareSchemas(db);
    if (token !== latest.current) return;
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    setDatabase(r.data.database); setSchemas(r.data.schemas);
  };
  const pickEntry = (e: ShareEntry | null) => {
    const token = ++latest.current;
    setEntry(e); setDatabase(e?.database ?? ""); setMountAs(e?.share ? slug(e.share) : "");
    setSchemas(null); setSchema(""); setTables(null); setPicked(new Set()); setFilter(""); setResult(null); setError(null); setBusy("");
    if (e) {
      // the name follows the share until someone types their own
      setSourceName((s) => (!s || s === autoName ? slug(e.share ?? e.database ?? "") : s));
      setAutoName(slug(e.share ?? e.database ?? ""));
      if (e.database && e.readable) void openSchemas(e.database, token);
    }
  };
  // the tables of a schema are listed as soon as it is chosen
  const pickSchema = async (name: string) => {
    const token = ++latest.current;
    setSchema(name); setTables(null); setPicked(new Set()); setFilter(""); setResult(null); setError(null);
    if (!name) { setBusy(""); return; }
    setBusy("tables");
    const r = await previewShare(database, name);
    if (token !== latest.current) return;
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    setTables(r.data.tables);
  };
  const mount = async (provider: string, share: string) => {
    const token = ++latest.current;
    setError(null); setBusy("mount");
    const r = await mountShare({ provider_account: provider, share_name: share, database: mountAs || slug(share) });
    if (token !== latest.current) return;
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    setDatabase(r.data.database); setSchemas(r.data.schemas);
  };
  const run = async () => {
    setError(null); setResult(null); setBusy("run");
    const r = await onboardShare(shareBody());
    setBusy("");
    if (!r.ok) { setError(explain(r.error).message); return; }
    setResult(r.data);
  };

  const unmounted = entry && !entry.database && !schemas;
  const noAccess = entry && entry.database && !entry.readable;
  const keysNeeded = ["SCD2", "MERGE"].includes(snap.bronze_load_type) || snap.load_type === "SCD2";
  const keysMissing = keysNeeded ? Array.from(picked).filter((t) => !(pks[t] ?? "").trim()) : [];
  const ready = !!database && !!schemas && !!schema && picked.size > 0 && NAME.test(sourceName) && !!landingDb && !!landingSchema
    && (!sync || (!!snap.bronze_schema.trim() && keysMissing.length === 0));
  const landed = !!result?.ok;
  const working = busy === "run";
  const label = (e: ShareEntry) => `${e.share ?? e.database}${!e.database ? " (not mounted)" : !e.readable ? " (no access)" : ""}`;

  return (
    <div className="surface space-y-7 p-6" data-testid="share-wizard">
      {error && <ErrorNote>{error}</ErrorNote>}

      {!landed && (
        <>
          <Section title="Source">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="share-pick">Database or share</Label>
                <Select id="share-pick" value={entry?.key ?? ""} onChange={(e) => pickEntry(entries.find((x) => x.key === e.target.value) ?? null)}>
                  <option value="">{entries.length ? "Choose a database or share" : "None visible to your role"}</option>
                  {entries.map((e) => <option key={e.key} value={e.key} data-testid={`share-${e.share ?? e.database}`}>{label(e)}</option>)}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="share-schema">Schema</Label>
                <div className="relative">
                  <Select id="share-schema" value={schema} disabled={!schemas} onChange={(e) => void pickSchema(e.target.value)}>
                    <option value="">{busy === "schemas" ? "Loading schemas..." : schemas ? (schemas.length ? "Choose a schema" : "No readable schema") : "Choose the database first"}</option>
                    {(schemas ?? []).map((s) => <option key={s.schema_name} value={s.schema_name}>{s.schema_name} ({n(s.tables)} tables)</option>)}
                  </Select>
                  {busy === "schemas" && <Loader2 className="pointer-events-none absolute right-8 top-2.5 h-4 w-4 animate-spin text-muted-foreground" />}
                </div>
              </div>
            </div>
            {noAccess && entry && (
              <div className="space-y-1 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100" data-testid="share-no-access">
                <p><span className="font-mono font-medium">{entry.database}</span> is mounted, but your current role cannot read it. Switch role, or ask an admin to run:</p>
                <code className="block rounded bg-background/70 px-2 py-1 font-mono text-xs">GRANT IMPORTED PRIVILEGES ON DATABASE {entry.database} TO ROLE &lt;your role&gt;;</code>
              </div>
            )}
            {unmounted && entry && (
              <div className="flex flex-wrap items-end gap-3 rounded-xl border bg-muted/30 p-3" data-testid="mount-share">
                <div className="space-y-1.5">
                  <Label htmlFor="mount-as">Mount as database</Label>
                  <Input id="mount-as" value={mountAs} onChange={(e) => setMountAs(e.target.value)} className="w-64 font-mono" />
                </div>
                <Button disabled={!!busy || !mountAs} onClick={() => void mount(entry.provider ?? "", entry.share ?? "")}>
                  {busy === "mount" ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Mount now
                </Button>
                <p className="basis-full text-xs text-muted-foreground">This share is not mounted yet. Mounting creates a read-only database from it.</p>
              </div>
            )}
          </Section>

          <Section title="Tables" testId="share-tables"
                   aside={tables && <span className="text-xs text-muted-foreground">{picked.size} of {tables.length} selected · {database}.{schema}</span>}>
            {!schema ? (
              <p className="rounded-xl border border-dashed px-4 py-5 text-center text-sm text-muted-foreground">Choose a schema to list its tables.</p>
            ) : busy === "tables" || !tables ? (
              <p className="flex items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading tables...
              </p>
            ) : tables.length ? (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Filter tables" aria-label="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} className="w-64 pl-8" />
                  </div>
                  <button type="button" className="text-xs font-medium text-primary hover:underline"
                          onClick={() => setPicked(new Set([...picked, ...shownTables.map((t) => t.table_name)]))}>Select {filter ? "shown" : "all"}</button>
                  <button type="button" className="text-xs text-muted-foreground hover:underline" onClick={() => setPicked(new Set())}>Clear</button>
                </div>
                <div className="grid max-h-72 gap-2 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
                  {shownTables.map((t) => {
                    const on = picked.has(t.table_name);
                    return (
                      <label key={t.table_name}
                             className={cn("flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm transition",
                               on ? "border-primary/50 bg-accent/50 ring-1 ring-primary/20" : "hover:border-primary/30 hover:bg-accent/30")}>
                        <input type="checkbox" checked={on} className="h-4 w-4 accent-[hsl(var(--primary))]"
                               onChange={(e) => setPicked((prev) => { const s = new Set(prev); if (e.target.checked) s.add(t.table_name); else s.delete(t.table_name); return s; })} />
                        <span className="flex-1 truncate font-mono text-xs">{t.table_name}</span>
                        <span className="text-[11px] tabular-nums text-muted-foreground">{n(t.row_count)}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            ) : <p className="text-sm text-muted-foreground">No tables in this schema.</p>}
          </Section>

          <Section title="Landing">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="src">Source system name</Label>
                <Input id="src" value={sourceName} onChange={(e) => setSourceName(e.target.value)} placeholder="PARTNER_SALES" />
                {!!sourceName && !NAME.test(sourceName) && <p className="text-xs text-destructive">Letters, digits and _, starting with a letter.</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="dom">Domain</Label>
                <Select id="dom" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                  <option value="">Decide later</option>
                  {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ldb">Landing database</Label>
                <Select id="ldb" value={landingDb} onChange={(e) => { setLandingDb(e.target.value); setLandingSchema(""); }}>
                  {(standard.includes(platform) ? standard : [platform, ...standard]).filter(Boolean).map((d) => <option key={d} value={d}>{d}{d === platform ? " (platform)" : ""}</option>)}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lsc">Landing schema</Label>
                <Select id="lsc" value={landingSchema} onChange={(e) => setLandingSchema(e.target.value)}>
                  <option value="" disabled>Choose a schema</option>
                  {landingSchemas.map((s) => <option key={s} value={s}>{s}</option>)}
                </Select>
              </div>
            </div>
            {picked.size > 0 && (
              <p className="truncate font-mono text-xs text-muted-foreground" data-testid="landing-preview"
                 title={Array.from(picked).map((t) => landingName(sourceName || "SOURCE", t)).join(", ")}>
                {landingDb}.{landingSchema || "?"}.{landingName(sourceName || "SOURCE", Array.from(picked)[0])}
                {picked.size > 1 && <span className="font-sans"> and {picked.size - 1} more</span>}
              </p>
            )}
          </Section>

          {sync && (
            <Section title="Other ingestion configuration" testId="share-snaplogic">
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-1.5"><Label htmlFor="sl-bdb">Bronze database</Label>
                  <Input id="sl-bdb" value={snap.bronze_database} placeholder="DEV_GDP_BRONZE_DB" onChange={(e) => setSnap({ ...snap, bronze_database: e.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="sl-bsc">Bronze schema</Label>
                  <Input id="sl-bsc" value={snap.bronze_schema} onChange={(e) => setSnap({ ...snap, bronze_schema: e.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="sl-desc">Description</Label>
                  <Input id="sl-desc" value={snap.source_system_desc} onChange={(e) => setSnap({ ...snap, source_system_desc: e.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="sl-lt">Landing load</Label>
                  <Select id="sl-lt" value={snap.load_type} onChange={(e) => setSnap({ ...snap, load_type: e.target.value })}>{LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></div>
                <div className="space-y-1.5"><Label htmlFor="sl-blt">Bronze load</Label>
                  <Select id="sl-blt" value={snap.bronze_load_type} onChange={(e) => setSnap({ ...snap, bronze_load_type: e.target.value })}>{BRONZE_LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></div>
                <div className="space-y-1.5"><Label htmlFor="sl-fq">Frequency</Label>
                  <Select id="sl-fq" value={snap.load_frequency} onChange={(e) => setSnap({ ...snap, load_frequency: e.target.value })}>
                    {FREQUENCIES.map((t) => <option key={t} value={t}>{t.replace("_", " ").toLowerCase()}</option>)}</Select></div>
                {snap.load_frequency !== "ON_DEMAND" && (
                  <div className="space-y-1.5"><Label htmlFor="sl-cron">Schedule (cron)</Label>
                    <Input id="sl-cron" className="font-mono text-xs" value={snap.schedule_cron} onChange={(e) => setSnap({ ...snap, schedule_cron: e.target.value })} /></div>
                )}
                <div className="space-y-1.5"><Label htmlFor="sl-af">Airflow source system</Label>
                  <Input id="sl-af" value={snap.airflow_source_system} onChange={(e) => setSnap({ ...snap, airflow_source_system: e.target.value })} /></div>
                <div className="space-y-1.5"><Label htmlFor="sl-sc">Silver command</Label>
                  <Input id="sl-sc" value={snap.silver_command_name} onChange={(e) => setSnap({ ...snap, silver_command_name: e.target.value })} /></div>
              </div>
              {picked.size > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium">Primary keys {keysNeeded
                    ? <span className="text-destructive">(needed for a {snap.bronze_load_type === "MERGE" ? "MERGE" : "SCD2"} load)</span>
                    : <span className="text-muted-foreground">(optional)</span>}</p>
                  {keysMissing.length > 0 && (
                    <p className="text-xs text-destructive">Add the keys of {keysMissing.join(", ")}, or choose a TRUNCATE bronze load and an APPEND or TRUNCATE load.</p>
                  )}
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {Array.from(picked).map((t) => (
                      <li key={t} className="flex items-center gap-2">
                        <span className="w-40 shrink-0 truncate font-mono text-xs" title={t}>{t}</span>
                        <Input aria-label={`Primary keys of ${t}`} className="h-8 text-xs" value={pks[t] ?? ""} placeholder="ID, SOURCE_KEY"
                               onChange={(e) => setPks({ ...pks, [t]: e.target.value })} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={snap.is_active} onChange={(e) => setSnap({ ...snap, is_active: e.target.checked })} className="h-4 w-4" />
                Start loading right away
              </label>
              <div className="space-y-2">
                <Button size="sm" variant="outline" disabled={!!busy || !ready} onClick={() => void previewSnap()} data-testid="share-snaplogic-preview">
                  {busy === "preview" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} Preview the configuration rows
                </Button>
                {snapRows && !snapRows.valid && (
                  <ul className="space-y-0.5 text-xs text-destructive" role="alert">
                    {Object.entries(snapRows.errors).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}
                  </ul>
                )}
                {snapRows && snapRows.valid && <SnapLogicRows preview={snapRows} />}
              </div>
            </Section>
          )}

          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">More options</summary>
            <div className="mt-3 space-y-4">
              <div className="max-w-sm space-y-1.5">
                <Label htmlFor="run">Run name</Label>
                <Input id="run" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder={`Share ${database || "database"}.${schema || "schema"}`} />
              </div>
              <div className="space-y-1.5">
                <p className="text-xs font-medium">The share is not listed</p>
                <div className="flex flex-wrap items-end gap-3">
                  <div className="space-y-1.5"><Label htmlFor="m-prov">Provider account</Label>
                    <Input id="m-prov" value={manual.provider} onChange={(e) => setManual({ ...manual, provider: e.target.value })} placeholder="ORG.ACCOUNT or locator" className="w-56" /></div>
                  <div className="space-y-1.5"><Label htmlFor="m-share">Share name</Label>
                    <Input id="m-share" value={manual.share} onChange={(e) => { setManual({ ...manual, share: e.target.value }); setMountAs(slug(e.target.value)); }} className="w-48" /></div>
                  <div className="space-y-1.5"><Label htmlFor="m-db">Mount as database</Label>
                    <Input id="m-db" value={mountAs} onChange={(e) => setMountAs(e.target.value)} className="w-48 font-mono" /></div>
                  <Button variant="outline" disabled={!!busy || !manual.provider || !manual.share}
                          onClick={() => { pickEntry(null); setSourceName(slug(manual.share)); void mount(manual.provider, manual.share); }}>Mount and continue</Button>
                </div>
              </div>
            </div>
          </details>
        </>
      )}

      {result && (
        <div className="space-y-4" data-testid="share-result">
          <ol className="space-y-1.5">
            {result.steps.map((s) => (
              <li key={s.step} className="flex items-start gap-2 text-sm">
                {s.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" /> : <XCircle className="mt-0.5 h-4 w-4 text-destructive" />}
                <span className="w-20 shrink-0 font-medium capitalize">{s.step}</span>
                <span className="text-muted-foreground">{s.detail}</span>
              </li>
            ))}
          </ol>
          {result.snaplogic && (
            <div className={cn("rounded-xl border p-3 text-sm", result.snaplogic.ok ? "border-success/30 bg-success/5" : "border-warning/30 bg-warning/5")}
                 data-testid="share-snaplogic-result">
              {result.snaplogic.ok ? (
                <p>Ingestion configuration written: master row {result.snaplogic.master_config_id}{result.snaplogic.master_created ? " (new)" : " (reused)"},
                  detail rows {(result.snaplogic.child_config_ids ?? []).join(", ")}.</p>
              ) : (
                <div className="space-y-2">
                  <p>The data landed, but the ingestion configuration was not written: {result.snaplogic.detail}</p>
                  {result.snaplogic.sync_id && !result.snaplogic.skipped && !retried && (
                    <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void retry(String(result.snaplogic?.sync_id))}>
                      {busy === "retry" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} Write them again
                    </Button>
                  )}
                  {retried && <p className="text-muted-foreground">{retried}</p>}
                </div>
              )}
            </div>
          )}
          <Link href={result.continue_at} className={buttonVariants({ variant: result.ok ? "default" : "outline" })}>
            {result.ok ? "Continue to profiling" : "Open the run to finish"} <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      )}

      {!landed && (
        <div className="flex items-center justify-end gap-3 border-t border-border/70 pt-5">
          {!ready && !working && <span className="text-xs text-muted-foreground">Choose the schema, at least one table and where it lands.</span>}
          <Button size="lg" disabled={!ready || !!busy} onClick={() => void run()} data-testid="share-run">
            {working ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} {working ? "Loading..." : "Load"}
          </Button>
        </div>
      )}
    </div>
  );
}
