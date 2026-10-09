import Link from "next/link";
import { Activity, FileCode2, ListChecks } from "lucide-react";
import { can, canAct } from "@/lib/types";
import { api, getRun, whoami } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { generateSoda } from "../pipeline-actions";
import { ColumnChecks, type Check } from "./column-checks";
import { SodaKit } from "./soda-kit";
import { QualityOverview, RunScanButton, ScanHistory } from "./quality-overview";
import { ApproveRemaining, BriefImport, DownloadMenu, PackGate } from "./toolbar";
import type { ScansPayload } from "./quality-shared";
import type { SttmLine } from "../sttm/sttm-board";

type SodaPayload = {
  checks: Check[];
  yaml: string;
  gx_suite?: Record<string, unknown> | null;
  brief: { title: string; content: string } | null;
  status: { total: number; proposed: number; approved: number; rejected: number };
};

const TABS = [
  { key: "checks", label: "Checks", icon: ListChecks },
  { key: "results", label: "Scan results", icon: Activity },
  { key: "kit", label: "Soda CLI kit", icon: FileCode2 },
] as const;
type Tab = (typeof TABS)[number]["key"];

const EMPTY_SCANS: ScansPayload = { scans: [], latest: [], history: {}, ready: false };

function Kpi({ label, value, tone }: { label: string; value: string | number; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "good" ? "text-success" : tone === "warn" ? "text-warning" : tone === "bad" ? "text-destructive" : "";
  return (
    <span className="whitespace-nowrap text-sm">
      <span className={`font-semibold tabular-nums ${color}`}>{value}</span>{" "}
      <span className="text-muted-foreground">{label}</span>
    </span>
  );
}

export default async function SodaPage({ params, searchParams }: {
  params: { runId: string };
  searchParams: { tab?: string };
}) {
  const [state, me, soda, contract, scans] = await Promise.all([
    getRun(params.runId),
    whoami(),
    api<SodaPayload>(`/api/runs/${params.runId}/soda`),
    api<{ lines: SttmLine[] }>(`/api/runs/${params.runId}/sttm`).catch(() => ({ lines: [] as SttmLine[] })),
    api<ScansPayload>(`/api/runs/${params.runId}/soda/scans`).catch(() => EMPTY_SCANS),
  ]);
  const legacy: Record<string, Tab> = { overview: "results", scans: "results" };
  const tab: Tab = TABS.find((t) => t.key === searchParams.tab)?.key ?? legacy[searchParams.tab ?? ""] ?? "checks";
  const kit = tab === "kit" && soda.status.total > 0
    ? await api<{ files: Record<string, string> }>(`/api/runs/${params.runId}/soda/kit`).catch((e: Error) => ({ error: e.message }))
    : null;

  const canGenerate = can(me, "RUN.OPERATE") && ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "DBT_PENDING", "DBT_GENERATING"].includes(state.current_state);
  const canImport = canAct(me, "SODA.EDIT") && ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING"].includes(state.current_state);
  const canReview = canAct(me, "SODA.EDIT") && ["SODA_REVIEW", "SODA_PENDING", "STTM_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED"].includes(state.current_state);
  const runnable = soda.status.total - soda.status.rejected;
  const last = scans.scans[0];
  const covered = new Set(soda.checks.filter((c) => c.target_column && c.status !== "REJECTED").map((c) => c.target_column!.toUpperCase()));
  const mapped = Array.from(new Set(contract.lines.filter((l) => l.mapping_type !== "UNMAPPED").map((l) => l.target_column.toUpperCase())));
  const uncovered = mapped.filter((c) => !covered.has(c)).length;
  const failing = last ? last.failed + last.errors : 0;
  const proposedIds = soda.checks.filter((c) => c.status === "PROPOSED").map((c) => c.expectation_id);

  return (
    <StageGate state={state} stage="SODA">
      <AiSuggestions runId={params.runId} stage="SODA" canAct={canImport} />

      <section className="space-y-3 rounded-xl border bg-card p-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h2 className="text-lg font-semibold" title="What the data must satisfy, column by column. Review, scan in Snowflake, ship to Soda.">
            Data Quality
          </h2>
          {soda.status.total > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Kpi label="checks" value={soda.status.total} />
              <Kpi label="to review" value={soda.status.proposed} tone={soda.status.proposed ? "warn" : "good"} />
              <Kpi label="approved" value={soda.status.approved} tone="good" />
              {mapped.length > 0 && uncovered > 0 && <Kpi label="columns without checks" value={uncovered} tone="warn" />}
              {last && <Kpi label="health" value={last.health ?? "–"}
                            tone={last.health == null ? undefined : last.health >= 90 ? "good" : last.health >= 70 ? "warn" : "bad"} />}
              {last && failing > 0 && <Kpi label="failing" value={failing} tone="bad" />}
              {state.current_state === "SODA_APPROVED" && (
                <span className="rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-xs font-medium text-success">pack approved</span>
              )}
            </div>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canGenerate && (
              <StageAction size="sm" variant={soda.status.total ? "ghost" : "default"}
                           label={soda.status.total ? "Regenerate" : "Generate checks"}
                           pendingLabel="Building checks…" action={generateSoda.bind(null, params.runId)} />
            )}
            {canImport && <BriefImport runId={params.runId} brief={soda.brief} />}
            {canReview && <ApproveRemaining runId={params.runId} ids={proposedIds} />}
            {runnable > 0 && can(me, "RUN.OPERATE") && <RunScanButton runId={params.runId} />}
            <DownloadMenu yaml={soda.yaml} gxSuite={soda.gx_suite} />
          </div>
        </div>

        <PackGate runId={params.runId} currentState={state.current_state}
                  complete={soda.status.total > 0 && soda.status.proposed === 0} />

        <nav role="tablist" aria-label="Data quality views" className="flex gap-1 border-b">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = t.key === tab;
            return (
              <Link key={t.key} href={`/runs/${params.runId}/soda?tab=${t.key}`} role="tab" aria-selected={active} scroll={false}
                    className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                <Icon className="h-4 w-4" />{t.label}
                {t.key === "results" && scans.scans.length > 0 && <span className="rounded-full bg-muted px-1.5 text-[11px] tabular-nums">{scans.scans.length}</span>}
              </Link>
            );
          })}
        </nav>

        {tab === "checks" && (
          soda.status.total === 0 && contract.lines.length === 0 ? (
            <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
              No checks yet. Generate them from the approved STTM, or import a client brief.
            </p>
          ) : (
            <ColumnChecks runId={params.runId} checks={soda.checks} sttmLines={contract.lines}
                          latest={scans.latest} history={scans.history} canReview={canReview} />
          )
        )}

        {tab === "results" && (
          <div className="space-y-8">
            <QualityOverview runId={params.runId} data={scans} checkCount={runnable} canScan={false} />
            {scans.scans.length > 1 && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">History</h3>
                <ScanHistory scans={scans.scans} />
              </div>
            )}
          </div>
        )}

        {tab === "kit" && (
          soda.status.total === 0 ? (
            <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">Generate checks first; the kit is built from them.</p>
          ) : kit && "files" in kit ? (
            <SodaKit runId={params.runId} files={kit.files} />
          ) : (
            <p role="alert" className="text-sm text-destructive">{kit && "error" in kit ? kit.error : "The kit could not be built."}</p>
          )
        )}
      </section>
    </StageGate>
  );
}
