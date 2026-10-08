"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle, ArrowRight, CalendarClock, CheckCircle2, Cloud, Database, FileSpreadsheet, Globe, HardDrive, Loader2,
  Plus, Search, Server, ShieldCheck, Workflow, XCircle,
} from "lucide-react";
import type { SourcesOverview } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Segmented, timeAgo } from "./oracle-ui";

export type ExternalRow = SourcesOverview["sources"][number];
type Status = "ok" | "attention" | "setup" | "empty" | "busy";
type Kind = "database" | "files" | "connector";

const FILES = new Set(["upload", "s3", "azure", "gcs"]);
const EXTRACTED = new Set(["oracle"]);

const LOOK: Record<string, { label: string; icon: typeof Database; tone: string }> = {
  oracle: { label: "Oracle", icon: HardDrive, tone: "from-red-600 to-orange-500" },
  upload: { label: "File upload", icon: FileSpreadsheet, tone: "from-sky-500 to-cyan-500" },
  s3: { label: "Amazon S3", icon: Cloud, tone: "from-amber-500 to-orange-500" },
  azure: { label: "Azure Blob", icon: Cloud, tone: "from-blue-600 to-sky-500" },
  gcs: { label: "Google Cloud Storage", icon: Cloud, tone: "from-emerald-500 to-teal-500" },
  postgres: { label: "PostgreSQL", icon: Database, tone: "from-indigo-500 to-blue-500" },
  sqlserver: { label: "SQL Server", icon: Server, tone: "from-rose-500 to-pink-500" },
  mysql: { label: "MySQL", icon: Database, tone: "from-cyan-600 to-sky-500" },
  salesforce: { label: "Salesforce", icon: Workflow, tone: "from-sky-500 to-blue-500" },
  rest_api: { label: "REST API", icon: Globe, tone: "from-violet-500 to-purple-500" },
};

function kindOf(x: ExternalRow): Kind {
  const t = x.connection_type ?? "";
  return FILES.has(t) ? "files" : EXTRACTED.has(t) ? "database" : "connector";
}

/** One status per source, in the order that matters: something running, something broken, setup missing,
 *  nothing landed yet, healthy. */
function statusOf(x: ExternalRow): { status: Status; label: string; detail: string } {
  const o = x.oracle;
  const running = o?.running ?? null;
  if (running || x.profiling_tables > 0 || x.active_jobs > 0) {
    return { status: "busy", label: running ? (running.kind === "ingest" ? "Loading" : "Profiling") : "Profiling",
             detail: running ? `${running.done}/${running.tables} tables` : `${x.profiling_tables} tables profiling` };
  }
  if (o && !o.ready) return { status: "setup", label: "Setup needed", detail: "Finish credentials and network access" };
  if (o?.health?.status === "fail") return { status: "attention", label: "Unreachable", detail: o.health.headline };
  if (x.failed_tables > 0) return { status: "attention", label: "Needs attention", detail: `${x.failed_tables} table(s) failed profiling` };
  if (o?.last_job && o.last_job.status !== "DONE") {
    return { status: "attention", label: "Last run had failures", detail: o.last_job.failed.slice(0, 3).join(", ") };
  }
  if (o?.health?.status === "warn") return { status: "attention", label: "Needs attention", detail: o.health.headline };
  if (!(x.landed_tables ?? 0) || x.health === "NOT_LANDED") {
    return { status: "empty", label: kindOf(x) === "connector" ? "Waiting for connector" : "Nothing landed",
             detail: kindOf(x) === "connector" ? "Landed by a Snowflake connector or CDC tool" : "Land data to start modeling" };
  }
  return { status: "ok", label: o ? "Connected" : "Healthy", detail: "" };
}

const STATUS_STYLE: Record<Status, { dot: string; text: string }> = {
  ok: { dot: "bg-success", text: "text-success" },
  busy: { dot: "bg-primary animate-pulse", text: "text-primary" },
  attention: { dot: "bg-warning", text: "text-warning" },
  setup: { dot: "bg-destructive", text: "text-destructive" },
  empty: { dot: "bg-muted-foreground/40", text: "text-muted-foreground" },
};

function Stat({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <div className="rounded-lg bg-muted/50 px-2 py-1.5 text-center">
      <p className="text-sm font-semibold tabular-nums">{value}</p>
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}

function SourceCard({ x, onManage, onOpen }: { x: ExternalRow; onManage: () => void; onOpen: () => void }) {
  const look = LOOK[x.connection_type ?? ""] ?? { label: x.connection_type ?? "External", icon: Globe, tone: "from-violet-500 to-purple-500" };
  const Icon = look.icon;
  const kind = kindOf(x);
  const s = statusOf(x);
  const style = STATUS_STYLE[s.status];
  const o = x.oracle;
  const landed = x.landed_tables ?? 0;
  const ready = Math.min(x.staged_tables, landed);
  const pct = landed ? Math.round((ready / landed) * 100) : 0;
  const where = o ? `${o.host}:${o.port}/${o.service}` : x.location ?? `${x.database_name}.${x.schema_name}`;
  const job = o?.last_job;
  const action = kind === "database" ? (o && !o.ready ? "Finish setup" : "Tables & loads")
    : kind === "files" ? "Land files" : "Details";

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-2xl border bg-card p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <div className={cn("pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-gradient-to-br opacity-15 blur-2xl", look.tone)} />
      <div className="flex items-start gap-3">
        <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-white shadow-sm", look.tone)}>
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold" title={x.source_system_name}>{x.source_system_name}</p>
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            {look.label}
            {o?.protocol === "tcps" && <span className="flex items-center gap-0.5 text-success"><ShieldCheck className="h-3 w-3" />TLS</span>}
            {o && <span>· {o.runtime === "snowflake" ? "runs in Snowflake" : "runs on API server"}</span>}
          </p>
        </div>
        <span className={cn("flex shrink-0 items-center gap-1.5 rounded-full border bg-background/70 px-2 py-0.5 text-[10px] font-medium", style.text)}>
          {s.status === "busy" ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} />}
          {s.label}
        </span>
      </div>

      <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground" title={where}>{where}</p>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <Stat value={landed} label="landed" />
        <Stat value={x.staged_tables} label="ready to model" />
        {o ? <Stat value={o.schedule ? <CalendarClock className="mx-auto h-4 w-4 text-primary" /> : "Off"} label="schedule" />
          : <Stat value={x.failed_tables || "–"} label="failed" />}
      </div>

      {landed > 0 && (
        <div className="mt-3" title={`${ready} of ${landed} landed tables profiled and ready to model`}>
          <div className="flex justify-between text-[10px] text-muted-foreground"><span>Ready to model</span><span>{pct}%</span></div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
            <div className={cn("h-full rounded-full", pct === 100 ? "bg-success" : "bg-primary")} style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}

      <p className="mt-3 flex min-h-[1.25rem] items-center gap-1.5 truncate text-[11px] text-muted-foreground">
        {s.status === "busy" ? <><Loader2 className="h-3 w-3 animate-spin text-primary" />{s.label} · {s.detail}</>
          : s.status === "attention" || s.status === "setup" ? <><AlertTriangle className="h-3 w-3 shrink-0 text-warning" /><span className="truncate" title={s.detail}>{s.detail}</span></>
          : job ? <>{job.status === "DONE" ? <CheckCircle2 className="h-3 w-3 text-success" /> : <XCircle className="h-3 w-3 text-destructive" />}
              Last {job.kind === "ingest" ? "load" : "profile"} {timeAgo(job.at)}{job.kind === "ingest" ? ` · ${job.rows.toLocaleString()} rows` : ""}</>
          : x.last_landed_at ? <><CheckCircle2 className="h-3 w-3 text-success" />Last landed {timeAgo(x.last_landed_at)}</>
          : <span>{s.detail || "Connected"}</span>}
      </p>

      <div className="mt-auto flex gap-2 pt-3">
        <Button size="sm" className="flex-1" variant={s.status === "setup" || s.status === "empty" ? "default" : "outline"} onClick={onManage}>
          {action}
        </Button>
        {landed > 0 && x.health === "HEALTHY" && (
          <Button size="sm" variant="ghost" onClick={onOpen}>Open <ArrowRight className="h-3.5 w-3.5" /></Button>
        )}
      </div>
    </div>
  );
}

/** Every external source at a glance: what it is, whether it is healthy, how much is landed and ready to model,
 *  and what is running; filter, search and add from here. */
export function ExternalSources({ sources, onManage, onOpen, onAdd }: {
  sources: ExternalRow[]; onManage: (x: ExternalRow) => void; onOpen: (x: ExternalRow) => void; onAdd: () => void;
}) {
  const [filter, setFilter] = useState<"all" | Kind | "attention">("all");
  const [query, setQuery] = useState("");
  const statuses = useMemo(() => Object.fromEntries(sources.map((x) => [x.source_system_id, statusOf(x).status])), [sources]);
  const counts = {
    ok: sources.filter((x) => statuses[x.source_system_id] === "ok").length,
    attention: sources.filter((x) => ["attention", "setup"].includes(statuses[x.source_system_id])).length,
    busy: sources.filter((x) => statuses[x.source_system_id] === "busy").length,
  };
  const landed = sources.reduce((n, x) => n + (x.landed_tables ?? 0), 0);
  const ready = sources.reduce((n, x) => n + x.staged_tables, 0);
  const shown = sources.filter((x) => (filter === "all" || (filter === "attention"
    ? ["attention", "setup"].includes(statuses[x.source_system_id]) : kindOf(x) === filter))
    && (!query || `${x.source_system_name} ${x.connection_type} ${x.oracle?.host ?? ""} ${x.location ?? ""}`.toLowerCase().includes(query.toLowerCase())));
  const order: Record<Status, number> = { busy: 0, setup: 1, attention: 2, ok: 3, empty: 4 };
  shown.sort((a, b) => order[statuses[a.source_system_id] as Status] - order[statuses[b.source_system_id] as Status]
    || a.source_system_name.localeCompare(b.source_system_name));

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Connected external sources</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted-foreground">
            <span>{sources.length} source{sources.length === 1 ? "" : "s"}</span>
            <span className="flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-success" />{counts.ok} healthy</span>
            {counts.attention > 0 && <span className="flex items-center gap-1 text-warning"><span className="h-1.5 w-1.5 rounded-full bg-warning" />{counts.attention} need attention</span>}
            {counts.busy > 0 && <span className="flex items-center gap-1 text-primary"><Loader2 className="h-3 w-3 animate-spin" />{counts.busy} running</span>}
            <span>{landed} tables landed · {ready} ready to model</span>
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {sources.length > 3 && (
            <div className="relative">
              <Search className="absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search sources" aria-label="Search external sources" className="h-8 w-44 pl-7 text-xs" />
            </div>
          )}
          <Segmented label="Source type" value={filter} onChange={setFilter}
                     options={[["all", "All"], ["database", "Databases"], ["files", "Files"], ["connector", "Connectors"], ["attention", `Attention${counts.attention ? ` · ${counts.attention}` : ""}`]] as const} />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((x) => <SourceCard key={x.source_system_id} x={x} onManage={() => onManage(x)} onOpen={() => onOpen(x)} />)}
        <button type="button" onClick={onAdd}
                className="flex min-h-[220px] flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed p-4 text-sm text-muted-foreground transition hover:border-primary/50 hover:bg-primary/5 hover:text-primary">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-muted"><Plus className="h-5 w-5" /></span>
          <span className="font-medium">Connect an external source</span>
          <span className="text-[11px]">Oracle, files, cloud storage, SaaS</span>
        </button>
      </div>
      {shown.length === 0 && sources.length > 0 && <p className="text-xs text-muted-foreground">No sources match.</p>}
    </section>
  );
}
