"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { CheckCircle2, CircleDashed, ExternalLink, FileCode2, Loader2, XCircle } from "lucide-react";
import type { Execution, GateRun, Incident, Suites } from "@/lib/hub-types";
import { Meter } from "@/components/hub/charts";
import { Drawer, Modal } from "@/components/hub/overlay";
import { EmptyState, ErrorNote, StatusPill } from "@/components/hub/states";
import { Button } from "@/components/ui/button";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { executionResults, gxSuite, type ResultRow } from "./actions";

const OUTCOME: Record<string, string> = { PASS: "text-emerald-700", FAIL: "text-rose-700", ERROR: "text-rose-700", WARN: "text-amber-700", REVIEW: "text-amber-700" };

export function Executions({ rows, kind }: { rows: Execution[]; kind: string }) {
  const [open, setOpen] = useState<Execution | null>(null);
  const [results, setResults] = useState<ResultRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const show = (e: Execution) => {
    setOpen(e); setResults(null); setError(null);
    start(async () => { const r = await executionResults(e.kind, e.id); if (r.ok) setResults(r.data.results); else setError(r.error); });
  };
  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {(["all", "soda", "qa"] as const).map((k) => (
          <Link key={k} href={`/dashboard/quality?tab=executions${k === "all" ? "" : `&kind=${k}`}`}
                className={cn("rounded-full border px-3 py-1 text-xs", kind === k ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground hover:text-foreground")}>
            {k === "all" ? "All" : k === "soda" ? "Data quality (Soda)" : "QA tests"}
          </Link>
        ))}
      </div>
      {!rows.length ? <EmptyState title="No executions yet">Run data quality checks or QA tests from a run or the QA workspace.</EmptyState> : (
        <div className="surface overflow-hidden">
          <Table>
            <THead><TR><TH>Kind</TH><TH>Run</TH><TH>Target</TH><TH className="text-right">Checks</TH><TH className="text-right">Failed</TH><TH className="w-44">Health</TH><TH>Started</TH></TR></THead>
            <TBody>
              {rows.map((e) => (
                <TR key={`${e.kind}${e.id}`} className="cursor-pointer" onClick={() => show(e)}>
                  <TD><span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", e.kind === "SODA" ? "bg-teal-50 text-teal-700" : "bg-indigo-50 text-indigo-700")}>{e.kind === "SODA" ? "Soda" : "QA"}</span></TD>
                  <TD className="text-xs">{e.run_id ? <Link onClick={(ev) => ev.stopPropagation()} href={`/runs/${e.run_id}/${e.kind === "SODA" ? "soda" : "qa"}`} className="text-primary hover:underline">{e.run_name ?? e.run_id.slice(0, 8)}</Link> : <span className="text-muted-foreground">table suite</span>}</TD>
                  <TD className="max-w-[16rem] truncate font-mono text-[11px]">{e.target ?? "-"}</TD>
                  <TD className="text-right tabular-nums">{e.total ?? "-"}</TD>
                  <TD className={cn("text-right tabular-nums", Number(e.failed) > 0 && "font-semibold text-rose-700")}>{(e.failed ?? 0) + (e.errors ?? 0)}</TD>
                  <TD><Meter value={e.health == null ? null : Number(e.health)} label="Health" /></TD>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{e.started_at?.slice(0, 16)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? `${open.kind === "SODA" ? "Data quality scan" : "QA run"}` : ""}
              subtitle={open ? `${open.run_name ?? open.target ?? ""} · ${open.started_at?.slice(0, 16)}` : undefined}>
        {error && <ErrorNote>{error}</ErrorNote>}
        {pending && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading results</p>}
        {results && (
          <ul className="divide-y divide-border/60">
            {results.map((r, i) => (
              <li key={i} className="py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className={cn("w-16 shrink-0 text-xs font-semibold", OUTCOME[String(r.outcome)] ?? "text-muted-foreground")}>{String(r.outcome).toLowerCase().replace(/_/g, " ")}</span>
                  <span className="flex-1 truncate font-medium">{String(r.title ?? `${r.check_type ?? ""} ${r.target_column ?? ""}`)}</span>
                  {r.failed_rows != null && Number(r.failed_rows) > 0 && <span className="text-xs tabular-nums text-rose-700">{Number(r.failed_rows).toLocaleString()} rows</span>}
                </div>
                {r.detail && <p className="ml-[4.5rem] mt-0.5 text-xs text-muted-foreground">{String(r.detail)}</p>}
              </li>
            ))}
            {!results.length && <li className="py-3 text-sm text-muted-foreground">No results recorded.</li>}
          </ul>
        )}
      </Drawer>
    </div>
  );
}

const GATE_LABEL: Record<string, string> = { data_quality: "Data quality", qa_tests: "QA tests", qa_signoff: "QA sign-off", incidents: "Incidents" };
const GATE_ICON = { PASS: CheckCircle2, FAIL: XCircle, PENDING: CircleDashed };

export function GatesTable({ runs }: { runs: GateRun[] }) {
  if (!runs.length) return <EmptyState title="No runs at the quality gates yet">A run appears here once it has a data quality scan or a QA run.</EmptyState>;
  return (
    <div className="surface overflow-hidden">
      <Table>
        <THead><TR><TH>Run</TH><TH>Readiness</TH>{Object.values(GATE_LABEL).map((g) => <TH key={g}>{g}</TH>)}<TH>State</TH></TR></THead>
        <TBody>
          {runs.map((r) => (
            <TR key={r.run_id}>
              <TD><Link href={`/runs/${r.run_id}`} className="font-medium text-primary hover:underline">{r.run_name}</Link><div className="font-mono text-[11px] text-muted-foreground">{r.target_model ?? ""}</div></TD>
              <TD><StatusPill status={r.status} /><span className="ml-2 text-xs tabular-nums text-muted-foreground">{r.score}%</span></TD>
              {Object.keys(GATE_LABEL).map((key) => {
                const g = r.gates.find((x) => x.gate === key);
                if (!g) return <TD key={key}>-</TD>;
                const Icon = GATE_ICON[g.status];
                return (
                  <TD key={key} title={g.detail}>
                    <span className={cn("inline-flex items-center gap-1 text-xs", g.status === "PASS" ? "text-emerald-700" : g.status === "FAIL" ? "text-rose-700" : "text-amber-700")}>
                      <Icon className="h-3.5 w-3.5" /> {g.status.toLowerCase()}
                    </span>
                    <div className="max-w-[10rem] truncate text-[10px] text-muted-foreground">{g.detail}</div>
                  </TD>
                );
              })}
              <TD className="text-xs text-muted-foreground">{r.current_state.toLowerCase().replace(/_/g, " ")}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

export function SuitesView({ suites }: { suites: Suites }) {
  const [gx, setGx] = useState<{ title: string; body: unknown } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const openGx = (runId: string) => start(async () => {
    const r = await gxSuite(runId);
    if (r.ok) setGx({ title: r.data.target_table, body: r.data.gx_suite }); else setError(r.error);
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {error && <div className="xl:col-span-2"><ErrorNote>{error}</ErrorNote></div>}
      <section className="surface p-4">
        <h3 className="text-sm font-semibold">QA test suites</h3>
        {!suites.qa_suites.length ? <p className="mt-2 text-sm text-muted-foreground">No suites yet. Build them in the QA workspace.</p> : (
          <ul className="mt-3 divide-y divide-border/60">
            {suites.qa_suites.map((s) => (
              <li key={s.suite_id} className="flex items-center gap-2 py-2 text-sm">
                <Link href={`/qa?tab=suites&suite=${s.suite_id}`} className="flex-1 truncate font-medium text-primary hover:underline">{s.name}</Link>
                {s.is_default && <span className="rounded-full bg-accent px-2 text-[10px] text-accent-foreground">default</span>}
                <span className="font-mono text-[11px] text-muted-foreground">{s.target_table ?? ""}</span>
                <span className="w-16 text-right text-xs tabular-nums">{s.tests} tests</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="surface p-4">
        <h3 className="text-sm font-semibold">Data quality check packs (Soda, Great Expectations)</h3>
        {!suites.check_packs.length ? <p className="mt-2 text-sm text-muted-foreground">No check packs yet. Generate checks in a run&apos;s data quality stage.</p> : (
          <ul className="mt-3 divide-y divide-border/60">
            {suites.check_packs.map((p) => (
              <li key={p.run_id} className="flex items-center gap-2 py-2 text-sm">
                <Link href={`/runs/${p.run_id}/soda`} className="flex-1 truncate font-medium text-primary hover:underline">{p.run_name ?? p.run_id.slice(0, 8)}</Link>
                <span className="text-xs tabular-nums text-muted-foreground">{p.approved}/{p.checks} approved</span>
                <a href={`/bff/soda-kit/${p.run_id}`} className="text-xs text-primary hover:underline">Soda kit</a>
                <Button size="sm" variant="ghost" className="h-7" onClick={() => openGx(p.run_id)} disabled={pending}><FileCode2 className="h-3.5 w-3.5" /> GX</Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <Modal open={!!gx} onClose={() => setGx(null)} wide title={`Great Expectations suite: ${gx?.title ?? ""}`} description="GX Core 1.x suite rendered from the run's current checks.">
        <pre className="max-h-[60vh] overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(gx?.body, null, 2)}</pre>
      </Modal>
    </div>
  );
}

export function IncidentsTable({ rows }: { rows: Incident[] }) {
  if (!rows.length) return <EmptyState title="No open Jira incidents">Issues linked to runs, tables or tests show here while they are open.</EmptyState>;
  return (
    <div className="surface overflow-hidden">
      <Table>
        <THead><TR><TH>Issue</TH><TH>Summary</TH><TH>Status</TH><TH>Linked to</TH><TH>Linked</TH></TR></THead>
        <TBody>
          {rows.map((i) => (
            <TR key={i.link_id}>
              <TD><Link href={`/qa?tab=triage&key=${encodeURIComponent(i.issue_key)}`} className="inline-flex items-center gap-1 font-mono text-xs font-medium text-primary hover:underline">{i.issue_key}<ExternalLink className="h-3 w-3" /></Link></TD>
              <TD className="max-w-md truncate text-sm">{i.summary ?? "-"}</TD>
              <TD className="text-xs">{i.status ?? "-"}</TD>
              <TD className="text-xs">{i.run_id ? <Link href={`/runs/${i.run_id}/qa?tab=jira`} className="text-primary hover:underline">{i.run_name ?? i.run_id.slice(0, 8)}</Link> : <span className="font-mono">{i.target_table ?? "-"}</span>}</TD>
              <TD className="whitespace-nowrap text-xs text-muted-foreground">{i.linked_at?.slice(0, 16)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}
