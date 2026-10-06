"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import {
  ArrowRight, CheckCircle2, CircleDashed, Database, Layers, Loader2, ShieldCheck, Sparkles, Table2, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ChecksTable } from "@/components/checks-table";
import { displayDomain, isModelTable, sourceSystemName } from "@/lib/catalog-display";
import { cn } from "@/lib/utils";
import type { OnboardingIntent } from "@/app/onboarding/intent-types";
import type { TableRow } from "@/app/onboarding/catalog-types";
import type { CachedProfile, LandingTargets, SourceOverview, StorageType } from "@/lib/types";
import { registerSourceStudio, setLandingTarget, validateAndLand } from "../source-actions";
import { runProfiling } from "../pipeline-actions";

const MAX_TABLES = 500;

type Target = { landing_database: string; landing_schema: string; storage_type: StorageType };
type StepStatus = "done" | "active" | "running" | "failed" | "waiting";
type Phase = "" | "register" | "land" | "profile";

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") return <CheckCircle2 className="h-5 w-5 text-success" />;
  if (status === "failed") return <XCircle className="h-5 w-5 text-destructive" />;
  if (status === "running") return <Loader2 className="h-5 w-5 animate-spin text-primary" />;
  return <CircleDashed className={cn("h-5 w-5", status === "active" ? "text-primary" : "text-muted-foreground/50")} />;
}

function Stat({ icon: Icon, label, value, detail }: {
  icon: typeof Database; label: string; value: React.ReactNode; detail?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-xl border bg-card p-4">
      <span className="rounded-lg bg-primary/10 p-2 text-primary"><Icon className="h-4 w-4" /></span>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <div className="truncate text-sm font-semibold">{value}</div>
        {detail && <div className="mt-0.5 text-xs text-muted-foreground">{detail}</div>}
      </div>
    </div>
  );
}

/** Source stage: executes the plan captured by the onboarding wizard. No re-selection, no model choice here. */
export function SourceStudio({
  runId, sourceName, sourceType, database, schema, intent, overview, initialTables = [],
  currentState, failedIn = null, failureReason = null, cachedProfiles = [], landingTargets = null, target,
}: {
  runId: string;
  sourceName: string;
  sourceType: string;
  database: string;
  schema: string;
  intent: OnboardingIntent | null;
  overview: SourceOverview;
  initialTables?: TableRow[];
  currentState: string;
  failedIn?: string | null;
  failureReason?: string | null;
  cachedProfiles?: CachedProfile[];
  landingTargets?: LandingTargets | null;
  target: Target;
}) {
  const objects = overview.objects ?? [];
  const name = sourceSystemName(database, schema, intent?.source.source_system_name || sourceName);
  const rowsByTable = useMemo(() => {
    const map = new Map<string, number | null>();
    initialTables.forEach((t) => map.set(t.table_name, t.row_count));
    objects.forEach((o) => map.set(o.object_name, o.row_count_estimate));
    return map;
  }, [initialTables, objects]);
  const available = Array.from(rowsByTable.keys()).filter((n) => !isModelTable(n));
  const cached = useMemo(() => new Map(cachedProfiles.map((p) => [p.table_name, p])), [cachedProfiles]);
  const landedBy = useMemo(() => {
    const map = new Map<string, SourceOverview["landing"][number]>();
    for (const l of overview.landing ?? []) if (!map.has(l.source_table)) map.set(l.source_table, l);
    return map;
  }, [overview.landing]);

  const fromRun = objects.filter((o) => o.selected_flag).map((o) => o.object_name);
  const planned = intent?.source.tables?.length ? intent.source.tables : available;
  const hasPlan = Boolean(intent) || fromRun.length > 0;
  const [picked, setPicked] = useState<string[]>(() => (fromRun.length ? fromRun : hasPlan ? planned : []));
  const tables = fromRun.length ? fromRun : hasPlan ? planned : picked;

  const [editingTarget, setEditingTarget] = useState(false);
  const [landingSchema, setLandingSchema] = useState(target.landing_schema);
  const [storageType, setStorageType] = useState<StorageType>(target.storage_type);
  const inPlace = storageType === "IN_PLACE";
  const targetDirty = landingSchema !== target.landing_schema || storageType !== target.storage_type;
  const landingSchemas = Array.from(new Set([target.landing_schema, ...(landingTargets?.schemas ?? [])]));

  const [phase, setPhase] = useState<Phase>("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [query, setQuery] = useState("");

  const preLanding = ["CREATED", "SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"];
  const preProfile = [...preLanding, "LANDING_COMPLETE", "PROFILING_PENDING", "PROFILING_RUNNING"];
  const failed = currentState === "FAILED";
  const landed = failed ? !preLanding.includes(failedIn ?? "") : !preLanding.includes(currentState);
  const profiled = !failed && !preProfile.includes(currentState);
  const canEditTarget = !landed;
  const checksFailed = !landed && (overview.checks ?? []).some((c) => c.checked_at === overview.checks[0]?.checked_at && c.status === "FAILED");

  const status = (step: Phase): StepStatus => {
    const done = step === "register" ? currentState !== "CREATED" : step === "land" ? landed : profiled;
    if (done) return "done";
    if (pending && phase === step) return "running";
    if (step === "land" && (failedIn === "ACCESS_VALIDATION" || failedIn === "LANDING_RUNNING" || checksFailed)) return "failed";
    if (step === "profile" && failedIn?.startsWith("PROFILING")) return "failed";
    return "waiting";
  };

  const cachedCount = tables.filter((t) => cached.has(t)).length;
  const actionLabel = profiled ? "" : landed ? `Profile ${tables.length} tables`
    : failed ? "Retry from the failed step"
      : inPlace ? `Validate & profile ${tables.length} tables` : `Validate, copy & profile ${tables.length} tables`;

  const run = () => start(async () => {
    setError("");
    if (targetDirty && canEditTarget && currentState !== "CREATED") {
      const saved = await setLandingTarget(runId, { landing_schema: landingSchema, storage_type: storageType });
      if (!saved.ok) { setError(saved.error); return; }
      setEditingTarget(false);
    }
    if (currentState === "CREATED") {
      setPhase("register");
      const reg = await registerSourceStudio(runId, {
        source_system_name: name, source_type: sourceType || "SNOWFLAKE_DATABASE", database, schema,
        landing_schema: landingSchema, storage_type: storageType,
      });
      if (!reg.ok) { setError(reg.error); setPhase(""); return; }
    }
    if (!landed) {
      setPhase("land");
      const res = await validateAndLand(runId, tables);
      if (!res.ok) { setError(res.error); setPhase(""); return; }
    }
    setPhase("profile");
    const prof = await runProfiling(runId);
    if (!prof.ok) setError(prof.error);
    setPhase("");
  });

  const visible = tables.filter((t) => t.toLowerCase().includes(query.toLowerCase()));
  const targets = intent?.targets ?? [];
  const lastAttempt = overview.checks?.length
    ? overview.checks.filter((c) => c.checked_at === overview.checks[0].checked_at) : [];

  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-3">
        <Stat icon={Database} label="Source connection" value={name}
              detail={<span className="font-mono">{database}.{schema} · {sourceType === "SNOWFLAKE_SHARE" ? "share" : "database"}</span>} />
        <Stat icon={Table2} label="Tables in plan" value={`${tables.length} table${tables.length === 1 ? "" : "s"}`}
              detail={cachedCount ? `${cachedCount} already profiled, reused from the profile store` : "None profiled before"} />
        <Stat icon={Layers} label="Modeling"
              value={targets.length ? `${targets.length} existing model${targets.length === 1 ? "" : "s"}` : "Decide after profiling"}
              detail={targets.length
                ? targets.slice(0, 3).map((t) => t.target_table).join(", ") + (targets.length > 3 ? "…" : "")
                : "Profiling proposes existing or new models"} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Run the source plan</CardTitle>
          <CardDescription>
            One action checks access and profiles every table where it lives. Nothing is copied unless you
            choose a copy mode. Profiles are saved to the shared profile store, so any later run reuses them.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <ol className="grid gap-3 md:grid-cols-3">
            {([
              ["land", ShieldCheck, inPlace ? "Validate access" : "Validate & copy", landed
                ? (inPlace ? `${landedBy.size} tables bound in place` : `${landedBy.size} copied into ${target.landing_schema}`)
                : inPlace ? "Read-only check; nothing is copied" : `Into ${target.landing_database}.${landingSchema}`],
              ["profile", Sparkles, "Profile & store", profiled
                ? "Saved to the profile store" : cachedCount ? `${cachedCount} of ${tables.length} reused from cache` : "Statistics, PII, keys, descriptions"],
              ["model", Layers, "Model", profiled ? "Ready for mapping" : "Unlocks after profiling"],
            ] as const).map(([step, Icon, title, detail], i) => {
              const s: StepStatus = step === "model" ? (profiled ? "active" : "waiting") : status(step);
              return (
                <li key={step} className={cn(
                  "flex items-start gap-3 rounded-xl border p-3",
                  s === "done" && "border-success/40 bg-success/5",
                  s === "running" && "border-primary bg-primary/5",
                  s === "failed" && "border-destructive/40 bg-destructive/5",
                )}>
                  <StepIcon status={s} />
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 text-sm font-semibold">
                      <Icon className="h-3.5 w-3.5 text-muted-foreground" /> {i + 1}. {title}
                    </p>
                    <p className="text-xs text-muted-foreground">{detail}</p>
                  </div>
                </li>
              );
            })}
          </ol>

          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm">
            {inPlace ? (
              <>
                <span className="text-muted-foreground">Reads tables in place from</span>
                <span className="font-mono text-xs">{database}.{schema}</span>
                <Badge variant="success">No copy</Badge>
              </>
            ) : (
              <>
                <span className="text-muted-foreground">Copies into</span>
                <span className="font-mono text-xs">{target.landing_database}.{landingSchema}</span>
                <Badge variant="outline">{storageType === "ICEBERG" ? "Iceberg" : "Managed"}</Badge>
              </>
            )}
            {canEditTarget && !editingTarget && (
              <button type="button" className="ml-auto text-xs font-medium text-primary hover:underline"
                      onClick={() => setEditingTarget(true)}>Change ingestion mode</button>
            )}
          </div>
          {editingTarget && canEditTarget && (
            <div className="flex flex-wrap items-end gap-4 rounded-lg border p-3">
              <div className={cn(storageType === "IN_PLACE" && "hidden")}>
                <label htmlFor="landing_schema" className="mb-1 block text-xs font-medium">
                  Landing schema in {target.landing_database}
                </label>
                <select id="landing_schema" value={landingSchema} onChange={(e) => setLandingSchema(e.target.value)}
                        className="h-9 rounded-md border bg-card px-2 text-sm">
                  {landingSchemas.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <fieldset>
                <legend className="mb-1 text-xs font-medium">Storage</legend>
                <div className="flex flex-wrap gap-3 text-sm">
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="storage_type" checked={storageType === "IN_PLACE"}
                           onChange={() => setStorageType("IN_PLACE")} /> Read in place (no copy)
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input type="radio" name="storage_type" checked={storageType === "MANAGED"}
                           onChange={() => setStorageType("MANAGED")} /> Copy to managed table
                  </label>
                  <label className={cn("flex items-center gap-1.5", !landingTargets?.iceberg_available && "opacity-50")}
                         title={landingTargets?.iceberg_available ? undefined : "Set PLATFORM_CONFIG LANDING_EXTERNAL_VOLUME to enable"}>
                    <input type="radio" name="storage_type" checked={storageType === "ICEBERG"}
                           disabled={!landingTargets?.iceberg_available} onChange={() => setStorageType("ICEBERG")} /> Copy to Iceberg table
                  </label>
                </div>
              </fieldset>
              <span className="text-xs text-muted-foreground">Applied when you run the plan.</span>
            </div>
          )}

          {failureReason && <p className="text-sm text-destructive">Last attempt failed: {failureReason}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

          <div className="flex flex-wrap items-center gap-3">
            {!profiled && (
              <Button size="lg" disabled={pending || tables.length === 0 || tables.length > MAX_TABLES} onClick={run}>
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending
                  ? phase === "register" ? "Registering source…" : phase === "land" ? (inPlace ? "Checking access…" : "Checking access and copying…") : "Profiling tables…"
                  : actionLabel}
              </Button>
            )}
            {profiled && (
              <>
                <Link href={`/runs/${runId}/mapping`} className={buttonVariants({ size: "lg" })}>
                  Continue to mapping <ArrowRight className="h-4 w-4" />
                </Link>
                <Link href={`/runs/${runId}/profile`} className={buttonVariants({ variant: "outline", size: "lg" })}>
                  View profiles and model suggestions
                </Link>
              </>
            )}
            {pending && <span className="text-xs text-muted-foreground">Running in Snowflake; large selections take a few minutes.</span>}
            {tables.length > MAX_TABLES && (
              <span className="text-sm text-destructive">At most {MAX_TABLES} tables per run.</span>
            )}
          </div>
        </CardContent>
      </Card>

      {lastAttempt.some((c) => c.status === "FAILED") && (
        <Card>
          <CardHeader>
            <CardTitle>Access check failed</CardTitle>
            <CardDescription>Ask an admin to grant access, or switch the Snowflake role in the sidebar, then retry.</CardDescription>
          </CardHeader>
          <CardContent><ChecksTable checks={lastAttempt} /></CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-end gap-3 space-y-0">
          <div className="mr-auto">
            <CardTitle>{hasPlan ? "Tables in this run" : "Choose tables"}</CardTitle>
            <CardDescription>
              {hasPlan
                ? `Chosen in the onboarding wizard (${displayDomain(intent?.domain_name) || "no domain yet"}). Landing and profile status update as the plan runs.`
                : "This run was created without a plan. Pick the tables to onboard."}
            </CardDescription>
          </div>
          {tables.length > 8 && (
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter tables" className="w-56" />
          )}
        </CardHeader>
        <CardContent>
          {!hasPlan && (
            <div className="mb-3 flex flex-wrap gap-2">
              {available.map((t) => (
                <button key={t} type="button" aria-pressed={picked.includes(t)}
                        onClick={() => setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))}
                        className={cn("rounded-full border px-3 py-1 text-xs",
                          picked.includes(t) ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                  {t}
                </button>
              ))}
            </div>
          )}
          <Table>
            <THead>
              <TR><TH>Table</TH><TH className="text-right">Source rows</TH><TH className="text-right">Landed rows</TH><TH>Landing</TH><TH>Profile</TH></TR>
            </THead>
            <TBody>
              {visible.map((t) => {
                const l = landedBy.get(t);
                const p = cached.get(t);
                return (
                  <TR key={t}>
                    <TD className="font-medium">{t}</TD>
                    <TD className="text-right tabular-nums">{l?.source_row_count ?? rowsByTable.get(t) ?? "—"}</TD>
                    <TD className="text-right tabular-nums">{l?.row_count ?? "—"}</TD>
                    <TD>
                      {l ? (
                        <span title={l.error_message ?? l.landing_table}>
                          <Badge variant={l.ingestion_status === "COMPLETE" ? "success" : "destructive"}>
                            {l.ingestion_status === "COMPLETE" ? "Landed" : l.ingestion_status}
                          </Badge>
                        </span>
                      ) : <Badge variant="outline">Pending</Badge>}
                    </TD>
                    <TD>
                      {p
                        ? <span title={`Profiled ${p.profiled_at.slice(0, 16)}`}><Badge variant="success">Profiled (cached)</Badge></span>
                        : <Badge variant="outline">Unprofiled</Badge>}
                    </TD>
                  </TR>
                );
              })}
              {visible.length === 0 && (
                <TR><TD colSpan={5} className="text-muted-foreground">{tables.length ? "No tables match." : "No tables chosen yet."}</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
