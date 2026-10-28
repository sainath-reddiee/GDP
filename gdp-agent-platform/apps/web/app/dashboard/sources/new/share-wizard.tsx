"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, Database, Loader2, Play, Search, Share2, XCircle } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, OnboardResult, ShareList, ShareTable, SnapLogicPreview, SnapLogicStatus } from "@/lib/hub-types";
import { SnapLogicRows } from "@/components/sources/snaplogic-preview";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote, explain } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { listSchemas, listShareSchemas, mountShare, onboardShare, previewShare, retrySnaplogic, snaplogicPreviewShare, snaplogicStatus,
  type ShareSchema } from "../actions";
import type { DomainOption } from "./wizard";

const BASE_STEPS = ["Database / Share", "Schema", "Tables", "Landing", "Load"] as const;
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

/** Onboard tables from a Snowflake database or share: pick the database or share (mount it if needed), one of its schemas and its tables,
 * choose where they land, then land them (each table unloaded once to Parquet, copied into landing) and continue to
 * profiling. Every choice is listed from Snowflake; changing one clears the choices made after it. */
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
        out.push({
          key: `db:${d.database_name}`,
          share: null,
          provider: null,
          database: d.database_name,
          readable: true,
          kind: d.type === "IMPORTED DATABASE" ? "share" : "database",
        });
      }
    }
    return out;
  }, [shares, databases]);

  const [step, setStep] = useState(0);
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "database" | "share">("all");
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
  // SnapLogic's config tables (Admin, SNAPLOGIC_SYNC): when the sync is on, a SnapLogic step asks what SnapLogic needs
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
  const STEPS: readonly string[] = sync ? ["Database / Share", "Schema", "Tables", "Landing", "SnapLogic", "Load"] : BASE_STEPS;
  const loadStep = STEPS.length - 1;
  const snapStep = sync ? 4 : -1;
  const snaplogicAnswers = () => sync ? {
    ...snap, schedule_cron: snap.load_frequency === "ON_DEMAND" ? "" : snap.schedule_cron,
    primary_keys: Object.fromEntries(Object.entries(pks).filter(([, v]) => v.trim())),
  } : undefined;
  const shareBody = () => ({
    database, schema, tables: Array.from(picked), source_system_name: sourceName, run_name: runName || undefined,
    domain_id: domainId || undefined, landing_database: landingDb || undefined, landing_schema: landingSchema || undefined,
    snaplogic: snaplogicAnswers(),
  });
  const previewSnap = () => start(async () => {
    const r = await snaplogicPreviewShare(shareBody());
    if (!r.ok) { setError(explain(r.error).message); return; }
    setSnapRows(r.data);
  });
  const retry = (syncId: string) => start(async () => {
    const r = await retrySnaplogic(syncId, { target: { bronze_schema: snap.bronze_schema, bronze_database: snap.bronze_database },
      load: { load_type: snap.load_type, bronze_load_type: snap.bronze_load_type, load_frequency: snap.load_frequency,
              job_trigger_time: snap.load_frequency === "ON_DEMAND" ? "" : snap.schedule_cron, is_active: snap.is_active },
      primary_keys: snaplogicAnswers()?.primary_keys ?? {} });
    if (!r.ok) { setError(explain(r.error).message); return; }
    if ("pending_approval" in r.data) { setRetried(r.data.detail); return; }
    setRetried(`SnapLogic rows written: master row ${r.data.snaplogic?.master_config_id}, detail rows ${(r.data.snaplogic?.child_config_ids ?? []).join(", ")}.`);
  });

  const shownEntries = entries.filter(
    (e) =>
      (kindFilter === "all" || e.kind === kindFilter) &&
      `${e.share ?? ""} ${e.provider ?? ""} ${e.database ?? ""}`.toLowerCase().includes(q.toLowerCase()),
  );
  const shownTables = useMemo(() => (tables ?? []).filter((t) => t.table_name.toLowerCase().includes(filter.toLowerCase())), [tables, filter]);

  // choosing earlier never leaves stale choices behind
  const pickEntry = (e: ShareEntry | null) => {
    setEntry(e); setDatabase(e?.database ?? ""); setMountAs(e?.share ? slug(e.share) : "");
    setSchemas(null); setSchema(""); setTables(null); setPicked(new Set()); setFilter(""); setResult(null); setError(null);
    // the name follows the share until someone types their own
    if (e) setSourceName((s) => (!s || s === autoName ? slug(e.share ?? e.database ?? "") : s));
    if (e) setAutoName(slug(e.share ?? e.database ?? ""));
  };
  const pickSchema = (name: string) => { setSchema(name); setTables(null); setPicked(new Set()); setFilter(""); setResult(null); };

  useEffect(() => {
    if (!landingDb || landingDb === landing?.database) { setLandingSchemas(landing?.schemas ?? []); return; }
    let live = true;
    listSchemas(landingDb).then((r) => { if (live && r.ok) setLandingSchemas(r.data.schemas.map((s) => s.schema_name)); });
    return () => { live = false; };
  }, [landingDb, landing]);

  const openSchemas = (db: string) => start(async () => {
    setError(null);
    const r = await listShareSchemas(db);
    if (!r.ok) { setError(explain(r.error).message); return; }
    setDatabase(r.data.database); setSchemas(r.data.schemas); setStep(1);
  });
  const mount = (provider: string, share: string) => start(async () => {
    setError(null);
    const r = await mountShare({ provider_account: provider, share_name: share, database: mountAs || slug(share) });
    if (!r.ok) { setError(explain(r.error).message); return; }
    setDatabase(r.data.database); setSchemas(r.data.schemas); setStep(1);
  });
  const openTables = () => start(async () => {
    setError(null);
    const r = await previewShare(database, schema);
    if (!r.ok) { setError(explain(r.error).message); return; }
    setTables(r.data.tables); setPicked(new Set()); setStep(2);
  });
  const run = () => start(async () => {
    setError(null); setResult(null);
    const r = await onboardShare(shareBody());
    if (!r.ok) { setError(explain(r.error).message); return; }
    setResult(r.data);
  });

  const unmounted = entry && !entry.database;
  const noAccess = entry && entry.database && !entry.readable;
  const keysNeeded = ["SCD2", "MERGE"].includes(snap.bronze_load_type) || snap.load_type === "SCD2";
  const keysMissing = keysNeeded ? Array.from(picked).filter((t) => !(pks[t] ?? "").trim()) : [];
  const canNext = [!!entry?.database && !!entry?.readable, !!schema, picked.size > 0, NAME.test(sourceName) && !!landingDb && !!landingSchema,
    ...(sync ? [!!snap.bronze_schema.trim() && keysMissing.length === 0] : []), true][step];
  const next = () => {
    if (step === 0) openSchemas(database);
    else if (step === 1) openTables();
    else setStep((s) => s + 1);
  };

  return (
    <div className="surface p-5" data-testid="share-wizard">
      <ol className="mb-5 flex flex-wrap gap-2" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} aria-current={i === step ? "step" : undefined}
              className={cn("flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset",
                i === step ? "bg-primary text-primary-foreground ring-primary" : i < step ? "bg-accent text-accent-foreground ring-primary/15" : "text-muted-foreground ring-border")}>
            <span className="tabular-nums">{i + 1}</span>{s}
          </li>
        ))}
      </ol>
      {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}

      {step === 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search databases, shares or providers" aria-label="Search databases or shares" className="w-72 pl-8" />
            </div>
            <div role="tablist" aria-label="Filter by object type" className="inline-flex rounded-lg border bg-muted/40 p-0.5 text-xs">
              {([
                ["all", `All (${entries.length})`],
                ["database", `Databases (${entries.filter((x) => x.kind === "database").length})`],
                ["share", `Shares (${entries.filter((x) => x.kind === "share").length})`],
              ] as const).map(([val, label]) => (
                <button
                  key={val}
                  type="button"
                  role="tab"
                  aria-selected={kindFilter === val}
                  onClick={() => setKindFilter(val)}
                  className={cn(
                    "rounded-md px-2.5 py-1 font-medium transition",
                    kindFilter === val ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-xs text-muted-foreground">{shownEntries.length} object{shownEntries.length === 1 ? "" : "s"} visible to your role</span>
          </div>
          {shownEntries.length ? (
            <div className="grid gap-2 md:grid-cols-2" role="radiogroup" aria-label="Databases and shares">
              {shownEntries.map((e) => (
                <button key={e.key} type="button" role="radio" aria-checked={entry?.key === e.key} onClick={() => pickEntry(e)} data-testid={`share-${e.share ?? e.database}`}
                        className={cn("flex min-w-0 items-start gap-3 rounded-xl border p-3 text-left transition",
                          entry?.key === e.key ? "border-primary/50 bg-accent/50 ring-1 ring-primary/20" : "hover:border-primary/30")}>
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                    {e.kind === "share" ? <Share2 className="h-4 w-4" /> : <Database className="h-4 w-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-sm font-medium">{e.share ?? e.database}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {e.provider ? `from ${e.provider}` : e.kind === "database" ? "Snowflake database" : "database created from a share"}{e.comment ? ` · ${e.comment}` : ""}
                    </span>
                  </span>
                  <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold",
                    e.database && e.readable ? "bg-success/10 text-success" : "bg-warning/10 text-warning")}
                        title={e.database && !e.readable ? "Mounted in the account, but your role cannot read it" : undefined}>
                    {e.kind === "database" ? "database" : !e.database ? "not mounted" : e.readable ? `mounted as ${e.database}` : `${e.database} · no access`}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
              {entries.length ? "No database or share matches the search." : "No databases or inbound shares are visible to your role."}
            </p>
          )}
          {noAccess && entry && (
            <div className="space-y-1 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100" data-testid="share-no-access">
              <p><span className="font-mono font-medium">{entry.database}</span> is mounted in this account, but your current role cannot read it, so its schemas and tables cannot be listed.</p>
              <p className="text-xs">Switch to a role that can read it (account menu), or ask an admin to run:</p>
              <code className="block rounded bg-background/70 px-2 py-1 font-mono text-xs">GRANT IMPORTED PRIVILEGES ON DATABASE {entry.database} TO ROLE &lt;your role&gt;;</code>
            </div>
          )}
          {unmounted && entry && (
            <div className="flex flex-wrap items-end gap-3 rounded-xl border p-3" data-testid="mount-share">
              <div className="space-y-1">
                <Label htmlFor="mount-as">Mount as database</Label>
                <Input id="mount-as" value={mountAs} onChange={(e) => setMountAs(e.target.value)} className="w-64 font-mono" />
              </div>
              <Button disabled={pending || !mountAs} onClick={() => mount(entry.provider ?? "", entry.share ?? "")}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Mount now
              </Button>
              <p className="basis-full text-xs text-muted-foreground">Mounting creates a read-only database from the share and needs the IMPORT SHARE privilege; an admin can do it once instead.</p>
            </div>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">The share is not listed</summary>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <div className="space-y-1"><Label htmlFor="m-prov">Provider account</Label>
                <Input id="m-prov" value={manual.provider} onChange={(e) => setManual({ ...manual, provider: e.target.value })} placeholder="ORG.ACCOUNT or locator" className="w-56" /></div>
              <div className="space-y-1"><Label htmlFor="m-share">Share name</Label>
                <Input id="m-share" value={manual.share} onChange={(e) => { setManual({ ...manual, share: e.target.value }); setMountAs(slug(e.target.value)); }} className="w-48" /></div>
              <div className="space-y-1"><Label htmlFor="m-db">Mount as database</Label>
                <Input id="m-db" value={mountAs} onChange={(e) => setMountAs(e.target.value)} className="w-48 font-mono" /></div>
              <Button variant="outline" disabled={pending || !manual.provider || !manual.share} onClick={() => { pickEntry(null); setSourceName(slug(manual.share)); mount(manual.provider, manual.share); }}>Mount and continue</Button>
            </div>
          </details>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-3">
          <p className="text-sm">Schemas in <span className="font-mono font-medium">{database}</span></p>
          {schemas && schemas.length ? (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="Schemas">
              {schemas.map((s) => (
                <button key={s.schema_name} type="button" role="radio" aria-checked={schema === s.schema_name} onClick={() => pickSchema(s.schema_name)}
                        className={cn("rounded-xl border p-3 text-left transition", schema === s.schema_name ? "border-primary/50 bg-accent/50 ring-1 ring-primary/20" : "hover:border-primary/30")}>
                  <span className="block font-mono text-sm font-medium">{s.schema_name}</span>
                  <span className="text-xs text-muted-foreground">{n(s.tables)} tables · {n(s.row_count)} rows</span>
                </button>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">This share has no schema with tables your role can read.</p>}
        </div>
      )}

      {step === 2 && tables && (
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Input placeholder="Filter tables" aria-label="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} className="max-w-xs" />
            <button className="text-xs text-primary hover:underline" onClick={() => setPicked(new Set([...picked, ...shownTables.map((t) => t.table_name)]))}>Select shown</button>
            <button className="text-xs text-muted-foreground hover:underline" onClick={() => setPicked(new Set())}>Clear</button>
            <span className="ml-auto text-xs text-muted-foreground">{picked.size} of {tables.length} selected · {database}.{schema}</span>
          </div>
          {tables.length ? (
            <div className="grid max-h-80 gap-1 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
              {shownTables.map((t) => (
                <label key={t.table_name} className="flex items-center gap-2 rounded-lg border border-border/70 px-3 py-2 text-sm hover:bg-accent/40">
                  <input type="checkbox" checked={picked.has(t.table_name)}
                         onChange={(e) => setPicked((prev) => { const s = new Set(prev); if (e.target.checked) s.add(t.table_name); else s.delete(t.table_name); return s; })} />
                  <span className="flex-1 truncate font-mono text-xs">{t.table_name}</span>
                  <span className="text-[11px] tabular-nums text-muted-foreground">{n(t.row_count)} rows</span>
                </label>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">No tables in this schema.</p>}
        </div>
      )}

      {step === 3 && (
        <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="src">Source system name</Label>
              <Input id="src" value={sourceName} onChange={(e) => setSourceName(e.target.value)} placeholder="PARTNER_SALES" />
              {!!sourceName && !NAME.test(sourceName) && <p className="text-xs text-destructive">Letters, digits and _, starting with a letter.</p>}
            </div>
            <div className="space-y-1">
              <Label htmlFor="run">Run name</Label>
              <Input id="run" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder={`Share ${database}.${schema}`} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="dom">Domain</Label>
              <Select id="dom" value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                <option value="">Decide later</option>
                {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
              </Select>
            </div>
          </div>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="ldb">Landing database</Label>
              <Select id="ldb" value={landingDb} onChange={(e) => { setLandingDb(e.target.value); setLandingSchema(""); }}>
                {(standard.includes(platform) ? standard : [platform, ...standard]).filter(Boolean).map((d) => <option key={d} value={d}>{d}{d === platform ? " (platform)" : ""}</option>)}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="lsc">Landing schema</Label>
              <Select id="lsc" value={landingSchema} onChange={(e) => setLandingSchema(e.target.value)}>
                <option value="" disabled>Choose a schema</option>
                {landingSchemas.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
            </div>
            <div className="rounded-xl border bg-muted/20 p-3 text-xs" data-testid="landing-preview">
              <p className="mb-1 font-medium">Lands as</p>
              <ul className="space-y-0.5 font-mono">
                {Array.from(picked).slice(0, 5).map((t) => <li key={t}>{landingDb}.{landingSchema || "?"}.{landingName(sourceName || "SOURCE", t)}</li>)}
                {picked.size > 5 && <li className="text-muted-foreground">and {picked.size - 5} more</li>}
              </ul>
            </div>
          </div>
        </div>
      )}

      {step === snapStep && (
        <div className="space-y-4" data-testid="share-snaplogic">
          <p className="text-xs text-muted-foreground">
            After the data lands, SnapLogic keeps loading it from {sync?.tables.detail}. These answers become its rows; nothing is
            written until the share has landed.
          </p>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1"><Label htmlFor="sl-bdb">Bronze database</Label>
              <Input id="sl-bdb" value={snap.bronze_database} placeholder="DEV_GDP_BRONZE_DB" onChange={(e) => setSnap({ ...snap, bronze_database: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="sl-bsc">Bronze schema</Label>
              <Input id="sl-bsc" value={snap.bronze_schema} onChange={(e) => setSnap({ ...snap, bronze_schema: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="sl-desc">Description</Label>
              <Input id="sl-desc" value={snap.source_system_desc} onChange={(e) => setSnap({ ...snap, source_system_desc: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="sl-lt">Landing load</Label>
              <Select id="sl-lt" value={snap.load_type} onChange={(e) => setSnap({ ...snap, load_type: e.target.value })}>{LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></div>
            <div className="space-y-1"><Label htmlFor="sl-blt">Bronze load</Label>
              <Select id="sl-blt" value={snap.bronze_load_type} onChange={(e) => setSnap({ ...snap, bronze_load_type: e.target.value })}>{BRONZE_LOAD_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></div>
            <div className="space-y-1"><Label htmlFor="sl-fq">Frequency</Label>
              <Select id="sl-fq" value={snap.load_frequency} onChange={(e) => setSnap({ ...snap, load_frequency: e.target.value })}>
                {FREQUENCIES.map((t) => <option key={t} value={t}>{t.replace("_", " ").toLowerCase()}</option>)}</Select></div>
            {snap.load_frequency !== "ON_DEMAND" && (
              <div className="space-y-1"><Label htmlFor="sl-cron">Schedule (cron)</Label>
                <Input id="sl-cron" className="font-mono text-xs" value={snap.schedule_cron} onChange={(e) => setSnap({ ...snap, schedule_cron: e.target.value })} /></div>
            )}
            <div className="space-y-1"><Label htmlFor="sl-af">Airflow source system</Label>
              <Input id="sl-af" value={snap.airflow_source_system} onChange={(e) => setSnap({ ...snap, airflow_source_system: e.target.value })} /></div>
            <div className="space-y-1"><Label htmlFor="sl-sc">Silver command</Label>
              <Input id="sl-sc" value={snap.silver_command_name} onChange={(e) => setSnap({ ...snap, silver_command_name: e.target.value })} /></div>
          </div>
          <div className="space-y-1.5">
            <p className="text-xs font-medium">Primary keys {["SCD2", "MERGE"].includes(snap.bronze_load_type) || snap.load_type === "SCD2"
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
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={snap.is_active} onChange={(e) => setSnap({ ...snap, is_active: e.target.checked })} className="h-4 w-4" />
            SnapLogic starts loading right away (IS_ACTIVE); leave off to switch it on later
          </label>
          <div className="space-y-2">
            <Button size="sm" variant="outline" disabled={pending} onClick={previewSnap} data-testid="share-snaplogic-preview">
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} Preview the SnapLogic rows
            </Button>
            {snapRows && !snapRows.valid && (
              <ul className="space-y-0.5 text-xs text-destructive" role="alert">
                {Object.entries(snapRows.errors).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}
              </ul>
            )}
            {snapRows && snapRows.valid && <SnapLogicRows preview={snapRows} />}
          </div>
        </div>
      )}

      {step === loadStep && (
        <div className="space-y-4">
          <ol className="grid gap-2 text-sm md:grid-cols-5" aria-label="What happens">
            {[["Register", `${sourceName.toUpperCase()} as a share source`], ["Check access", `read ${picked.size} table${picked.size === 1 ? "" : "s"} in ${database}.${schema}`],
              ["Unload", "each table once to Parquet on CORE.LANDING_STAGE"], ["Copy into landing", `${landingDb}.${landingSchema} with exact column types`],
              ["Profile", "the run continues to profiling"]].map(([t, d], i) => {
              const done = result?.steps.find((s) => s.step === ["register", "access", "landing", "landing", "run"][i]);
              return (
                <li key={t} className="rounded-xl border p-3">
                  <span className="flex items-center gap-1.5 font-medium">
                    {done ? (done.ok ? <CheckCircle2 className="h-4 w-4 text-success" /> : <XCircle className="h-4 w-4 text-destructive" />) : <span className="grid h-4 w-4 place-items-center rounded-full bg-muted text-[10px]">{i + 1}</span>}
                    {t}
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{d}</span>
                </li>
              );
            })}
          </ol>
          {result && (
            <ol className="space-y-1.5" data-testid="share-result">
              {result.steps.map((s) => (
                <li key={s.step} className="flex items-start gap-2 text-sm">
                  {s.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" /> : <XCircle className="mt-0.5 h-4 w-4 text-destructive" />}
                  <span className="w-20 shrink-0 font-medium capitalize">{s.step}</span>
                  <span className="text-muted-foreground">{s.detail}</span>
                </li>
              ))}
            </ol>
          )}
          {result?.snaplogic && (
            <div className={cn("rounded-xl border p-3 text-sm", result.snaplogic.ok ? "border-success/30 bg-success/5" : "border-warning/30 bg-warning/5")}
                 data-testid="share-snaplogic-result">
              {result.snaplogic.ok ? (
                <p>SnapLogic rows written: master row {result.snaplogic.master_config_id}{result.snaplogic.master_created ? " (new)" : " (reused)"},
                  detail rows {(result.snaplogic.child_config_ids ?? []).join(", ")}.</p>
              ) : (
                <div className="space-y-2">
                  <p>The data landed, but SnapLogic&apos;s rows were not written: {result.snaplogic.detail}</p>
                  {result.snaplogic.sync_id && !result.snaplogic.skipped && !retried && (
                    <Button size="sm" variant="outline" disabled={pending} onClick={() => retry(String(result.snaplogic?.sync_id))}>
                      {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} Write them again with the answers above
                    </Button>
                  )}
                  {retried && <p className="text-muted-foreground">{retried}</p>}
                </div>
              )}
            </div>
          )}
          {result && (
            <Link href={result.continue_at} className={buttonVariants({ variant: result.ok ? "default" : "outline" })}>
              {result.ok ? "Continue to profiling" : "Open the run to finish"} <ArrowRight className="h-4 w-4" />
            </Link>
          )}
        </div>
      )}

      <div className="mt-6 flex items-center justify-between border-t border-border/70 pt-4">
        <Button variant="ghost" disabled={step === 0 || pending} onClick={() => { setError(null); setStep((s) => s - 1); }}><ArrowLeft className="h-4 w-4" /> Back</Button>
        {step < loadStep ? (
          <Button disabled={!canNext || pending} onClick={next} data-testid="share-next">
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Next <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button disabled={pending || !!result?.ok} onClick={run} data-testid="share-run">
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {pending ? "Landing..." : "Land and continue"}
          </Button>
        )}
      </div>
    </div>
  );
}
