"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Loader2, Play, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { runQualityScan } from "../pipeline-actions";
import {
  HealthRing, OUTCOME_LABEL, OutcomeBadge, Spark, fmtDuration, fmtNumber,
  type CheckResult, type HistoryPoint, type Outcome, type ScanRow, type ScansPayload,
} from "./quality-shared";
import { useScrollLock } from "@/components/use-scroll-lock";

const DIMENSIONS = ["completeness", "uniqueness", "validity", "timeliness", "schema", "consistency", "accuracy"];
const ORDER: Outcome[] = ["FAIL", "ERROR", "WARN", "NOT_EVALUATED", "PASS"];

function ago(iso: string) {
  const t = Date.parse(iso.replace(" ", "T"));
  if (Number.isNaN(t)) return iso;
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

export function RunScanButton({ runId, disabled, label = "Run scan" }: { runId: string; disabled?: boolean; label?: string }) {
  const [pending, start] = useTransition();
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" size="sm" disabled={disabled || pending}
              onClick={() => start(async () => {
                setMessage(null);
                const r = await runQualityScan(runId);
                if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
                const s = r.data;
                setMessage({ ok: true, text: `${s.passed} pass · ${s.warned} warn · ${s.failed} fail · ${s.not_evaluated} not evaluated in ${fmtDuration(s.duration_ms)}` });
              })}>
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        {pending ? "Scanning in Snowflake…" : label}
      </Button>
      {message && <p role={message.ok ? "status" : "alert"} className={`max-w-sm text-right text-xs ${message.ok ? "text-muted-foreground" : "text-destructive"}`}>{message.text}</p>}
    </div>
  );
}

export function QualityOverview({ runId, data, checkCount, canScan }: {
  runId: string; data: ScansPayload; checkCount: number; canScan: boolean;
}) {
  const last = data.scans[0];
  const [outcome, setOutcome] = useState<string>("ISSUES");
  const [dimension, setDimension] = useState<string>("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<CheckResult | null>(null);

  const dims = useMemo(() => DIMENSIONS.map((d) => {
    const rows = data.latest.filter((r) => r.dimension === d);
    const count = (o: Outcome) => rows.filter((r) => r.outcome === o).length;
    return { d, total: rows.length, pass: count("PASS"), warn: count("WARN"), fail: count("FAIL") + count("ERROR"), ne: count("NOT_EVALUATED") };
  }).filter((x) => x.total > 0), [data.latest]);

  const datasets = useMemo(() => {
    const map = new Map<string, CheckResult[]>();
    for (const r of data.latest) map.set(r.target_table || "(dataset)", [...(map.get(r.target_table || "(dataset)") ?? []), r]);
    return Array.from(map.entries());
  }, [data.latest]);

  const q = query.trim().toLowerCase();
  const rows = data.latest
    .filter((r) => outcome === "ALL" || (outcome === "ISSUES" ? !["PASS", "NOT_EVALUATED"].includes(r.outcome) : r.outcome === outcome))
    .filter((r) => !dimension || r.dimension === dimension)
    .filter((r) => !q || [r.target_column, r.kind, r.check_type, r.detail].join(" ").toLowerCase().includes(q))
    .sort((a, b) => ORDER.indexOf(a.outcome) - ORDER.indexOf(b.outcome));

  if (!data.ready) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-10 text-center">
        <p className="text-base font-medium">Scan results are not switched on here yet</p>
        <p className="max-w-lg text-sm text-muted-foreground">
          This environment is missing the tables that keep scan results. Once the latest release is deployed,
          Run scan executes every check in Snowflake and the results, failed rows and history appear here.
        </p>
      </div>
    );
  }

  if (!last) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed p-10 text-center">
        <p className="text-base font-medium">No scans yet</p>
        <p className="max-w-lg text-sm text-muted-foreground">
          {checkCount
            ? `Run the ${checkCount} checks inside Snowflake. The scan runs against the built model when dbt has produced it, otherwise against today's source data, and keeps every result for later.`
            : "Generate or add checks on the Checks tab first, then run a scan here."}
        </p>
        {canScan && checkCount > 0 && <RunScanButton runId={runId} label="Run first scan" />}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 lg:grid-cols-[auto_1fr_auto]">
        <div className="flex items-center gap-5 rounded-lg border p-4">
          <HealthRing health={last.health} passed={last.passed} warned={last.warned} failed={last.failed + last.errors} />
          <div className="space-y-1 text-sm">
            <Stat color="bg-success" label="Passed" value={last.passed} />
            <Stat color="bg-warning" label="Warned" value={last.warned} />
            <Stat color="bg-destructive" label="Failed" value={last.failed + last.errors} />
            <Stat color="bg-muted-foreground" label="Not evaluated" value={last.not_evaluated} />
          </div>
        </div>
        <div className="rounded-lg border p-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Checks by dimension</p>
          <div className="space-y-2">
            {dims.map((x) => (
              <button key={x.d} type="button" onClick={() => setDimension(dimension === x.d ? "" : x.d)}
                      className={`grid w-full grid-cols-[110px_1fr_auto] items-center gap-3 rounded px-1 text-left text-xs hover:bg-muted/50 ${dimension === x.d ? "bg-muted" : ""}`}>
                <span className="capitalize">{x.d}</span>
                <span className="flex h-2.5 overflow-hidden rounded-full bg-muted">
                  <span className="bg-success" style={{ width: `${(x.pass / x.total) * 100}%` }} />
                  <span className="bg-warning" style={{ width: `${(x.warn / x.total) * 100}%` }} />
                  <span className="bg-destructive" style={{ width: `${(x.fail / x.total) * 100}%` }} />
                </span>
                <span className="tabular-nums text-muted-foreground">{x.pass}/{x.total}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col justify-between gap-3 rounded-lg border p-4 text-sm">
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Last scan</p>
            <p className="font-medium" title={last.started_at}>{ago(last.started_at)}</p>
            <p className="text-xs text-muted-foreground">
              {last.mode === "MODEL" ? "Built model" : "Source data"} · {fmtDuration(last.duration_ms)}
              {last.rows_scanned != null ? ` · ${fmtNumber(last.rows_scanned)} rows` : ""}
            </p>
            <p className="max-w-[16rem] truncate font-mono text-[11px] text-muted-foreground" title={last.target}>{last.target}</p>
          </div>
          {canScan && <RunScanButton runId={runId} />}
        </div>
      </div>

      {last.mode === "SOURCE" && (
        <p className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs">
          The model is not built yet, so this scan used today&apos;s source data. Checks that only make sense on the model show as not evaluated.
          Scan again after dbt has built it for the full picture.
        </p>
      )}

      {datasets.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {datasets.map(([t, rs]) => (
            <Badge key={t} variant={rs.some((r) => r.outcome === "FAIL") ? "destructive" : "outline"}>
              {t}: {rs.filter((r) => r.outcome === "PASS").length}/{rs.length} pass
            </Badge>
          ))}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="mr-auto text-sm font-medium">Check results</p>
          <Input className="w-56" placeholder="Search column, check…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <Select aria-label="Outcome filter" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="ISSUES">Issues only</option>
            <option value="ALL">All outcomes</option>
            {ORDER.map((o) => <option key={o} value={o}>{OUTCOME_LABEL[o]}</option>)}
          </Select>
          {dimension && <Button size="sm" variant="ghost" onClick={() => setDimension("")}>{dimension} <X className="h-3 w-3" /></Button>}
        </div>
        {rows.length === 0 ? (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            {outcome === "ISSUES" ? "No failing or warning checks in the last scan." : "No results match the filters."}
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Outcome</TH><TH>Check</TH><TH>Dimension</TH><TH className="text-right">Measured</TH>
                <TH>Threshold</TH><TH className="text-right">Failed rows</TH><TH>Trend</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.expectation_id} className="cursor-pointer hover:bg-muted/40" onClick={() => setOpen(r)}>
                  <TD><OutcomeBadge outcome={r.outcome} /></TD>
                  <TD>
                    <span className="font-mono text-xs">{r.target_column || r.target_table}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{r.kind || r.check_type}</span>
                  </TD>
                  <TD className="text-xs capitalize">{r.dimension}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{fmtNumber(r.measured)}</TD>
                  <TD className="font-mono text-xs">{r.threshold || "–"}</TD>
                  <TD className="text-right font-mono text-xs tabular-nums">{r.failed_rows ? fmtNumber(r.failed_rows) : "–"}</TD>
                  <TD><Spark points={data.history[r.expectation_id] ?? []} /></TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </div>

      {open && <ResultDrawer result={open} history={data.history[open.expectation_id] ?? []} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Stat({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <p className="flex items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
      <span className="w-28 text-muted-foreground">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
    </p>
  );
}

export function ResultDrawer({ result, history, onClose }: { result: CheckResult; history: HistoryPoint[]; onClose: () => void }) {
  useScrollLock();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const sample = result.sample ?? [];
  const cols = sample.length ? Object.keys(sample[0]) : [];
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close result" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label="Check result"
             className="relative flex h-full w-[720px] max-w-full flex-col overflow-hidden border-l bg-background shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b p-4">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <OutcomeBadge outcome={result.outcome} />
              <Badge variant="outline" className="capitalize">{result.dimension}</Badge>
              <Badge variant={result.severity === "FAIL" ? "destructive" : "outline"}>{result.severity}</Badge>
            </div>
            <p className="font-mono text-sm">{result.target_table}{result.target_column ? `.${result.target_column}` : ""}</p>
            <p className="text-xs text-muted-foreground">{result.kind || result.check_type}</p>
          </div>
          <Button size="sm" variant="ghost" aria-label="Close" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
        <div className="flex-1 space-y-4 overflow-auto overscroll-contain p-4 text-sm">
          <div className="grid grid-cols-3 gap-3">
            <Tile label="Measured" value={fmtNumber(result.measured)} />
            <Tile label="Threshold" value={result.threshold || "–"} />
            <Tile label="Failed rows" value={result.failed_rows ? fmtNumber(result.failed_rows) : "0"} />
          </div>
          {result.detail && <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs">{result.detail}</p>}
          {history.length > 1 && (
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">History</p>
              <Spark points={history} />
              <div className="flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                {history.slice(-6).reverse().map((h, i) => (
                  <span key={i} className="rounded border px-1.5 py-0.5">{h.at.slice(0, 16)} · {OUTCOME_LABEL[h.outcome]}{h.measured != null ? ` · ${fmtNumber(h.measured)}` : ""}</span>
                ))}
              </div>
            </div>
          )}
          {sample.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Failed row samples ({sample.length}) · PII masked
              </p>
              <div className="max-h-72 overflow-auto rounded-md border">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-muted">
                    <tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-mono font-medium">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {sample.map((row, i) => (
                      <tr key={i} className="border-t">
                        {cols.map((c) => <td key={c} className="whitespace-nowrap px-2 py-1 font-mono">{row[c] == null ? <span className="text-muted-foreground">NULL</span> : String(row[c])}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {result.sql_text && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">SQL executed</p>
                <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(result.sql_text || "")}>Copy</Button>
              </div>
              <pre className="max-h-72 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs leading-relaxed">{result.sql_text}</pre>
            </div>
          )}
          <p className="text-xs text-muted-foreground">Ran in {fmtDuration(result.duration_ms)}.</p>
        </div>
      </aside>
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 truncate font-mono text-base font-semibold tabular-nums" title={value}>{value}</p>
    </div>
  );
}

export function ScanHistory({ scans }: { scans: ScanRow[] }) {
  if (!scans.length) return <p className="text-sm text-muted-foreground">No scans yet.</p>;
  const points = [...scans].reverse();
  const w = 560, h = 80;
  const x = (i: number) => (points.length === 1 ? w / 2 : (i / (points.length - 1)) * (w - 20) + 10);
  const y = (v: number | null) => h - 8 - ((v ?? 0) / 100) * (h - 16);
  return (
    <div className="space-y-4">
      {points.length > 1 && (
        <div className="rounded-lg border p-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Health over time</p>
          <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full" preserveAspectRatio="none" role="img" aria-label="Health trend">
            <polyline fill="none" stroke="hsl(var(--primary))" strokeWidth={2}
                      points={points.map((p, i) => `${x(i)},${y(p.health)}`).join(" ")} />
            {points.map((p, i) => (
              <circle key={p.scan_id} cx={x(i)} cy={y(p.health)} r={3}
                      fill={p.failed ? "hsl(var(--destructive))" : p.warned ? "hsl(var(--warning))" : "hsl(var(--success))"}>
                <title>{`${p.started_at.slice(0, 16)} · health ${p.health ?? "–"}`}</title>
              </circle>
            ))}
          </svg>
        </div>
      )}
      <Table>
        <THead>
          <TR>
            <TH>When</TH><TH>Target</TH><TH className="text-right">Health</TH><TH className="text-right">Pass</TH>
            <TH className="text-right">Warn</TH><TH className="text-right">Fail</TH><TH className="text-right">Not eval.</TH>
            <TH className="text-right">Duration</TH><TH>By</TH>
          </TR>
        </THead>
        <TBody>
          {scans.map((s) => (
            <TR key={s.scan_id}>
              <TD className="whitespace-nowrap text-xs" title={s.started_at}>{ago(s.started_at)}</TD>
              <TD><Badge variant="outline">{s.mode === "MODEL" ? "model" : "source"}</Badge></TD>
              <TD className="text-right font-semibold tabular-nums">{s.health ?? "–"}</TD>
              <TD className="text-right tabular-nums text-success">{s.passed}</TD>
              <TD className="text-right tabular-nums text-warning">{s.warned}</TD>
              <TD className="text-right tabular-nums text-destructive">{s.failed + s.errors}</TD>
              <TD className="text-right tabular-nums text-muted-foreground">{s.not_evaluated}</TD>
              <TD className="text-right text-xs tabular-nums">{fmtDuration(s.duration_ms)}</TD>
              <TD className="max-w-[10rem] truncate text-xs text-muted-foreground" title={s.created_by || ""}>{s.created_by} · {s.triggered_by}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}
