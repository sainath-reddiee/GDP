import Link from "next/link";
import { Activity, FileCode2, History, ListChecks } from "lucide-react";
import { api, getRun } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateSoda } from "../pipeline-actions";
import { SodaBoard } from "./soda-board";
import { SodaGate } from "./soda-gate";
import { SodaKit } from "./soda-kit";
import { QualityOverview, ScanHistory } from "./quality-overview";
import type { ScansPayload } from "./quality-shared";
import type { SttmLine } from "../sttm/sttm-board";

type SodaPayload = {
  checks: {
    expectation_id: string; target_table: string; target_column: string | null; check_type: string;
    check_definition?: Record<string, unknown> | null; severity: string; origin: string;
    client_requirement: string | null; status: string; version: number; sodacl?: string;
    evidence?: string | null;
    backtest?: { status: "PASS" | "FAIL" | "NOT_EVALUATED"; observed?: number; percent?: number; detail?: string } | null;
  }[];
  yaml: string;
  gx_suite?: Record<string, unknown> | null;
  brief: { title: string; content: string } | null;
  status: { total: number; proposed: number; approved: number; rejected: number };
};

const TABS = [
  { key: "overview", label: "Overview", icon: Activity },
  { key: "checks", label: "Checks", icon: ListChecks },
  { key: "scans", label: "Scan history", icon: History },
  { key: "kit", label: "Soda CLI kit", icon: FileCode2 },
] as const;
type Tab = (typeof TABS)[number]["key"];

const EMPTY_SCANS: ScansPayload = { scans: [], latest: [], history: {}, ready: false };

export default async function SodaPage({ params, searchParams }: {
  params: { runId: string };
  searchParams: { tab?: string };
}) {
  const [state, soda, contract, scans] = await Promise.all([
    getRun(params.runId),
    api<SodaPayload>(`/api/runs/${params.runId}/soda`),
    api<{ lines: SttmLine[] }>(`/api/runs/${params.runId}/sttm`).catch(() => ({ lines: [] })),
    api<ScansPayload>(`/api/runs/${params.runId}/soda/scans`).catch(() => EMPTY_SCANS),
  ]);
  const requested = TABS.find((t) => t.key === searchParams.tab)?.key;
  const tab: Tab = requested ?? (soda.status.total === 0 || scans.scans.length === 0 ? "checks" : "overview");
  const kit = tab === "kit" && soda.status.total > 0
    ? await api<{ files: Record<string, string> }>(`/api/runs/${params.runId}/soda/kit`).catch((e: Error) => ({ error: e.message }))
    : null;

  const canGenerate = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "DBT_PENDING", "DBT_GENERATING"].includes(state.current_state);
  const canImport = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING"].includes(state.current_state);
  const canReview = ["SODA_REVIEW", "SODA_PENDING", "STTM_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED"].includes(state.current_state);
  const runnable = soda.status.total - soda.status.rejected;
  const last = scans.scans[0];
  const counts: Partial<Record<Tab, string>> = {
    overview: last?.health != null ? `${last.health}` : undefined,
    checks: soda.status.total ? `${soda.status.total}` : undefined,
    scans: scans.scans.length ? `${scans.scans.length}` : undefined,
  };

  return (
    <StageGate state={state} stage="SODA">
      <Card>
        <CardHeader>
          <CardTitle>Data Quality</CardTitle>
          <CardDescription>
            Checks come from the approved STTM, the client brief, the data profile and your own custom SQL. Review them,
            scan them inside Snowflake for rich results with failed-row samples, and take the same checks to your own
            environment with the Soda CLI kit. Approved check sets and recurring failures are kept in the knowledge base.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <nav role="tablist" aria-label="Data quality views" className="flex flex-wrap gap-1 border-b">
            {TABS.map((t) => {
              const Icon = t.icon;
              const active = t.key === tab;
              return (
                <Link key={t.key} href={`/runs/${params.runId}/soda?tab=${t.key}`} role="tab" aria-selected={active} scroll={false}
                      className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
                  <Icon className="h-4 w-4" />
                  {t.label}
                  {counts[t.key] && <span className="rounded-full bg-muted px-1.5 text-[11px] tabular-nums">{counts[t.key]}</span>}
                </Link>
              );
            })}
          </nav>

          {tab === "overview" && (
            <QualityOverview runId={params.runId} data={scans} checkCount={runnable} canScan={runnable > 0} />
          )}

          {tab === "checks" && (
            <>
              {canGenerate && (
                <StageAction
                  label="Generate Data Quality checks"
                  pendingLabel="Building SodaCL from the STTM, profile and client brief…"
                  action={generateSoda.bind(null, params.runId)}
                />
              )}
              <SodaGate
                runId={params.runId}
                currentState={state.current_state}
                complete={soda.status.total > 0 && soda.status.proposed === 0}
                proposed={soda.status.proposed}
                approved={soda.status.approved}
                rejected={soda.status.rejected}
              />
              <SodaBoard
                runId={params.runId}
                checks={soda.checks}
                yaml={soda.yaml}
                gxSuite={soda.gx_suite}
                brief={soda.brief}
                sttmLines={contract.lines}
                canImport={canImport}
                canReview={canReview}
                latest={scans.latest}
                history={scans.history}
              />
            </>
          )}

          {tab === "scans" && <ScanHistory scans={scans.scans} />}

          {tab === "kit" && (
            soda.status.total === 0 ? (
              <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">Generate checks first; the kit is built from them.</p>
            ) : kit && "files" in kit ? (
              <SodaKit runId={params.runId} files={kit.files} />
            ) : (
              <p role="alert" className="text-sm text-destructive">{kit && "error" in kit ? kit.error : "The kit could not be built."}</p>
            )
          )}
        </CardContent>
      </Card>
      {tab === "checks" && <AiSuggestions runId={params.runId} stage="SODA" canAct={canImport} />}
    </StageGate>
  );
}
