"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check as CheckIcon, ChevronDown, Database, Loader2, Pencil, Plus, Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { addSodaCheck, saveSodaDecisions } from "../pipeline-actions";
import type { SttmLine } from "../sttm/sttm-board";
import { CheckEditor, KINDS, cleanDefinition, draftFrom, type Draft } from "./check-editor";
import { OutcomeBadge, Spark, fmtNumber, type CheckResult, type HistoryPoint } from "./quality-shared";

export type Check = {
  expectation_id: string;
  target_table: string;
  target_column: string | null;
  check_type: string;
  check_definition?: Record<string, unknown> | string | null;
  severity: string;
  origin: string;
  client_requirement: string | null;
  status: string;
  sodacl?: string;
  evidence?: string | null;
  backtest?: { status: "PASS" | "FAIL" | "NOT_EVALUATED"; observed?: number; percent?: number; detail?: string } | null;
};

const DATASET = "__dataset__";
type Filter = "all" | "review" | "failing" | "none";

function defOf(c: Check): Record<string, unknown> {
  const d = c.check_definition;
  if (!d) return {};
  if (typeof d === "string") { try { return JSON.parse(d); } catch { return {}; } }
  return d;
}

function thresholdText(t: unknown): string {
  if (!t || typeof t !== "object") return "";
  const o = t as Record<string, unknown>;
  if (Array.isArray(o.between)) return `between ${o.between[0]} and ${o.between[1]}`;
  return `${o.op ?? ""} ${o.value ?? ""}`.trim();
}

/** A check in plain words: what it promises about the data. */
export function describeCheck(c: Check): string {
  const d = defOf(c);
  const kind = String(d.kind ?? c.check_type).toLowerCase();
  const list = (v: unknown, n = 4) => {
    const a = Array.isArray(v) ? v.map(String) : [];
    return a.length > n ? `${a.slice(0, n).join(", ")} +${a.length - n}` : a.join(", ");
  };
  switch (kind) {
    case "row_count":
      return d.min != null && d.max != null ? `Row count between ${d.min} and ${d.max}` : `Row count above ${d.gt ?? 0}`;
    case "change_over_time":
      return `Row count moves at most −${d.max_decrease_percent ?? 20}% / +${d.max_increase_percent ?? 50}% per scan`;
    case "not_null": return Array.isArray(d.missing_values) && d.missing_values.length ? `No missing values (incl. ${list(d.missing_values, 3)})` : "No missing values";
    case "missing_percent": return `At most ${d.max_percent ?? 0}% missing`;
    case "unique": {
      const cols = Array.isArray(d.columns) && d.columns.length > 1 ? (d.columns as string[]).join(" + ") : "";
      return cols ? `No duplicates on ${cols}` : "No duplicate values";
    }
    case "duplicate_percent": return `At most ${d.max_percent ?? 0}% duplicates`;
    case "accepted_values": return `Only ${list(d.values)}`;
    case "regex": return `Matches ${String(d.pattern ?? "")}`;
    case "format": return `Valid ${d.format ?? "format"}`;
    case "range":
      return d.min != null && d.max != null ? `Between ${d.min} and ${d.max}` : d.min != null ? `At least ${d.min}` : `At most ${d.max}`;
    case "max_length": return `At most ${d.max} characters`;
    case "freshness": return `Fresher than ${d.threshold ?? "1d"}`;
    case "schema": {
      const parts = [Array.isArray(d.required) && d.required.length && `requires ${list(d.required, 3)}`,
                     Array.isArray(d.forbidden) && d.forbidden.length && `forbids ${list(d.forbidden, 3)}`,
                     d.types && Object.keys(d.types as object).length && `${Object.keys(d.types as object).length} typed columns`].filter(Boolean);
      return `Schema ${parts.join(", ") || "is as expected"}`;
    }
    case "reference": return `Exists in ${d.reference_table}${d.reference_column ? `.${d.reference_column}` : ""}`;
    case "failed_rows": return d.query ? "Custom rule (SQL query)" : `Custom rule: ${d.condition}`;
    case "metric": return `${d.name ?? "Metric"} ${thresholdText(d.threshold)}`;
    case "avg": case "min": case "max": case "sum": case "stddev":
      return `${kind === "stddev" ? "Std dev" : kind[0].toUpperCase() + kind.slice(1)} ${thresholdText(d.threshold)}`;
    default: return c.check_type.replace(/_/g, " ").toLowerCase();
  }
}

function kindLabel(c: Check) {
  const kind = String(defOf(c).kind ?? "");
  return KINDS.find((k) => k.kind === kind)?.label ?? c.check_type;
}

type ColumnRow = {
  key: string; name: string; line?: SttmLine; checks: Check[];
  proposed: number; failing: boolean;
};

const QUICK: { label: string; kind: string; def: Record<string, unknown> }[] = [
  { label: "Not null", kind: "not_null", def: { kind: "not_null" } },
  { label: "Unique", kind: "unique", def: { kind: "unique" } },
  { label: "Valid values", kind: "accepted_values", def: { kind: "accepted_values", values: [] } },
  { label: "Range", kind: "range", def: { kind: "range" } },
  { label: "Custom SQL", kind: "failed_rows", def: { kind: "failed_rows" } },
];

export function ColumnChecks({ runId, checks, sttmLines, latest, history, canReview }: {
  runId: string;
  checks: Check[];
  sttmLines: SttmLine[];
  latest: CheckResult[];
  history: Record<string, HistoryPoint[]>;
  canReview: boolean;
}) {
  const router = useRouter();
  const results = useMemo(() => new Map(latest.map((r) => [r.expectation_id, r])), [latest]);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const columns = useMemo<ColumnRow[]>(() => {
    const byCol = new Map<string, Check[]>();
    for (const c of checks) {
      const key = c.target_column ? c.target_column.toUpperCase() : DATASET;
      byCol.set(key, [...(byCol.get(key) ?? []), c]);
    }
    const names = new Set<string>([...sttmLines.map((l) => l.target_column.toUpperCase()), ...Array.from(byCol.keys())]);
    names.delete(DATASET);
    const row = (key: string, name: string): ColumnRow => {
      const cs = byCol.get(key) ?? [];
      return {
        key, name, checks: cs, line: sttmLines.find((l) => l.target_column.toUpperCase() === key),
        proposed: cs.filter((c) => c.status === "PROPOSED").length,
        failing: cs.some((c) => ["FAIL", "ERROR"].includes(results.get(c.expectation_id)?.outcome ?? "")),
      };
    };
    const ordered = sttmLines.map((l) => l.target_column.toUpperCase()).filter((n, i, a) => a.indexOf(n) === i);
    const rest = Array.from(names).filter((n) => !ordered.includes(n)).sort();
    return [row(DATASET, "Dataset"), ...[...ordered, ...rest].map((n) => row(n, n))];
  }, [checks, sttmLines, results]);

  const visible = columns.filter((c) => {
    if (query && c.key !== DATASET && !c.name.toLowerCase().includes(query.toLowerCase())) return false;
    if (filter === "review") return c.proposed > 0;
    if (filter === "failing") return c.failing;
    if (filter === "none") return c.checks.length === 0 && c.key !== DATASET;
    return true;
  });

  const initial = () => {
    if (typeof window !== "undefined") {
      const col = new URLSearchParams(window.location.search).get("col");
      if (col && columns.some((c) => c.key === col)) return col;
    }
    return columns.find((c) => c.proposed > 0)?.key ?? columns.find((c) => c.checks.length)?.key ?? DATASET;
  };
  const [selected, setSelected] = useState<string>(initial);
  const current = columns.find((c) => c.key === selected) ?? columns[0];

  const pick = (key: string) => {
    setSelected(key);
    const url = new URL(window.location.href);
    url.searchParams.set("col", key);
    window.history.replaceState(null, "", url.toString());
  };

  const railRef = useRef<HTMLDivElement>(null);
  const onRailKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const i = visible.findIndex((c) => c.key === current.key);
    const next = visible[Math.max(0, Math.min(visible.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      pick(next.key);
      railRef.current?.querySelector<HTMLButtonElement>(`[data-col="${next.key}"]`)?.scrollIntoView({ block: "nearest" });
    }
  };

  const counts = {
    all: columns.length,
    review: columns.filter((c) => c.proposed > 0).length,
    failing: columns.filter((c) => c.failing).length,
    none: columns.filter((c) => c.key !== DATASET && c.checks.length === 0).length,
  };

  return (
    <div className="grid min-h-[560px] gap-0 overflow-hidden rounded-xl border lg:grid-cols-[300px_minmax(0,1fr)]">
      <div className="flex max-h-[75vh] flex-col border-b bg-muted/20 lg:border-b-0 lg:border-r">
        <div className="space-y-2 border-b p-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input className="pl-8" placeholder="Find a column" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find a column" />
          </div>
          <div className="flex flex-wrap gap-1">
            {([["all", "All"], ["review", "To review"], ["failing", "Failing"], ["none", "No checks"]] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setFilter(k)} aria-pressed={filter === k}
                      className={cn("rounded-full border px-2.5 py-0.5 text-xs", filter === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40",
                                    k === "failing" && counts.failing && filter !== k && "border-destructive/40 text-destructive",
                                    k === "none" && counts.none && filter !== k && "border-warning/40 text-warning")}>
                {label} <span className="tabular-nums opacity-70">{counts[k]}</span>
              </button>
            ))}
          </div>
        </div>
        <div ref={railRef} role="listbox" aria-label="Columns" tabIndex={0} onKeyDown={onRailKey} className="flex-1 overflow-y-auto p-1.5 outline-none">
          {visible.length === 0 && <p className="p-3 text-xs text-muted-foreground">No columns match.</p>}
          {visible.map((c) => {
            const active = c.key === current.key;
            const dot = c.failing ? "bg-destructive" : c.proposed ? "bg-warning" : c.checks.length ? "bg-success" : "bg-muted-foreground/40";
            return (
              <button key={c.key} type="button" role="option" aria-selected={active} data-col={c.key} onClick={() => pick(c.key)}
                      className={cn("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition",
                                    active ? "bg-card shadow-sm ring-1 ring-primary/40" : "hover:bg-card/70")}>
                <span className={cn("h-2 w-2 shrink-0 rounded-full", dot)} />
                <span className="min-w-0 flex-1">
                  <span className={cn("flex items-center gap-1.5 truncate text-sm", c.key === DATASET ? "font-semibold" : "font-mono text-[13px]")}>
                    {c.key === DATASET && <Database className="h-3.5 w-3.5 text-muted-foreground" />}{c.name}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {c.key === DATASET ? "table-level checks" : c.line ? `${c.line.mapping_type.toLowerCase()} · ${c.line.target_datatype}` : "not in STTM"}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  {c.proposed > 0 && <span className="rounded-full bg-warning/15 px-1.5 text-[10px] font-medium text-warning">{c.proposed}</span>}
                  <span className={cn("min-w-[1.5rem] rounded-full px-1.5 text-center text-[11px] tabular-nums", c.checks.length ? "bg-muted font-medium" : "text-muted-foreground")}>
                    {c.checks.length}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <ColumnPane key={current.key} runId={runId} column={current} results={results} history={history}
                  canReview={canReview} columnNames={columns.filter((c) => c.key !== DATASET).map((c) => c.name)}
                  onAdded={(id) => { router.refresh(); return id; }} />
    </div>
  );
}

function ColumnPane({ runId, column, results, history, canReview, columnNames, onAdded }: {
  runId: string; column: ColumnRow; results: Map<string, CheckResult>; history: Record<string, HistoryPoint[]>;
  canReview: boolean; columnNames: string[]; onAdded: (id: string) => void;
}) {
  const isDataset = column.key === DATASET;
  const [adding, setAdding] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, start] = useTransition();
  const [bulkNote, setBulkNote] = useState<string | null>(null);
  const proposed = column.checks.filter((c) => c.status === "PROPOSED");
  const order = (c: Check) => (c.status === "PROPOSED" ? 0 : c.status === "APPROVED" ? 1 : 2);
  const sorted = [...column.checks].sort((a, b) => order(a) - order(b));

  const startAdd = (def: Record<string, unknown>) =>
    setAdding({ target_column: isDataset ? "" : column.name, severity: "FAIL", requirement: "", definition: def });

  const saveNew = () => start(async () => {
    if (!adding) return;
    setError(""); setNotice("");
    const r = await addSodaCheck(runId, {
      target_column: adding.target_column || null, severity: adding.severity,
      requirement: adding.requirement, definition: cleanDefinition(adding.definition),
    });
    if (!r.ok) { setError(r.error); return; }
    setAdding(null);
    setNotice("Check added. Approve it below when it looks right.");
    onAdded(r.data.expectation_id);
  });

  const approveAll = () => start(async () => {
    const note = (bulkNote ?? "").trim();
    if (note.length < 8) { setError("Give a short justification (8+ characters)."); return; }
    setError(""); setNotice("");
    const r = await saveSodaDecisions(runId, proposed.map((c) => ({
      expectation_id: c.expectation_id, decision: "APPROVED", justification: note,
      requirement: c.client_requirement || undefined,
    })));
    if (!r.ok) { setError(r.error); return; }
    setBulkNote(null);
    setNotice(`${r.data.decided} approved.`);
  });

  const line = column.line;
  return (
    <div className="flex max-h-[75vh] min-w-0 flex-col">
      <div className="space-y-2 border-b p-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h3 className={cn("text-lg font-semibold", !isDataset && "font-mono")}>{isDataset ? "Dataset checks" : column.name}</h3>
            {line ? (
              <p className="truncate text-xs text-muted-foreground" title={line.transformation ?? undefined}>
                <span className="font-mono">{[line.source_table, line.source_column].filter(Boolean).join(".") || "derived"}</span>
                {" → "}<span className="font-mono">{column.name}</span>
                {" · "}{line.mapping_type.toLowerCase()} · {line.target_datatype}
                {line.transformation ? <> · <span className="font-mono">{line.transformation}</span></> : null}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">{isDataset ? "Row count, schema, freshness and custom SQL rules for the whole table." : "Not mapped in the STTM."}</p>
            )}
          </div>
          {canReview && (
            <div className="flex shrink-0 gap-2">
              {proposed.length > 1 && (
                <Button size="sm" variant="outline" disabled={pending} onClick={() => setBulkNote(bulkNote == null ? "Checks match the STTM and the client quality need." : null)}>
                  <CheckIcon className="h-3.5 w-3.5" />Approve all {proposed.length}
                </Button>
              )}
              <Button size="sm" onClick={() => (adding ? setAdding(null) : startAdd({ kind: isDataset ? "row_count" : "not_null" }))}>
                {adding ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}{adding ? "Cancel" : "Add check"}
              </Button>
            </div>
          )}
        </div>
        {bulkNote != null && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 p-2">
            <Input className="min-w-[16rem] flex-1" value={bulkNote} onChange={(e) => setBulkNote(e.target.value)} aria-label="Justification" />
            <Button size="sm" disabled={pending} onClick={approveAll}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Approve {proposed.length}</Button>
          </div>
        )}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {notice && <p role="status" className="text-xs text-success">{notice}</p>}
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {adding && (
          <div className="space-y-3 rounded-xl border border-primary/40 bg-primary/5 p-4">
            <p className="text-sm font-semibold">New check{isDataset ? "" : ` on ${column.name}`}</p>
            <CheckEditor draft={adding} onChange={setAdding} columns={columnNames} />
            <Button size="sm" disabled={pending} onClick={saveNew}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Add as proposed</Button>
          </div>
        )}

        {sorted.length === 0 && !adding && (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed p-8 text-center">
            <p className="text-sm font-medium">No checks on {isDataset ? "the dataset" : column.name} yet</p>
            {canReview && (
              <div className="flex flex-wrap justify-center gap-2">
                {(isDataset
                  ? [{ label: "Row count", def: { kind: "row_count", gt: 0 } }, { label: "Freshness", def: { kind: "freshness", threshold: "1d" } },
                     { label: "Schema", def: { kind: "schema" } }, { label: "Custom SQL", def: { kind: "failed_rows" } }]
                  : QUICK).map((q) => (
                  <button key={q.label} type="button" onClick={() => startAdd(q.def)}
                          className="rounded-full border bg-card px-3 py-1 text-xs hover:border-primary/50 hover:text-primary">
                    <Plus className="mr-1 inline h-3 w-3" />{q.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {sorted.map((c) => (
          <CheckCard key={c.expectation_id} runId={runId} check={c} result={results.get(c.expectation_id)}
                     history={history[c.expectation_id] ?? []} canReview={canReview} columnNames={columnNames} />
        ))}
      </div>
    </div>
  );
}

function CheckCard({ runId, check, result, history, canReview, columnNames }: {
  runId: string; check: Check; result?: CheckResult; history: HistoryPoint[]; canReview: boolean; columnNames: string[];
}) {
  const [editing, setEditing] = useState<Draft | null>(null);
  const [details, setDetails] = useState(false);
  const [why, setWhy] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const proposed = check.status === "PROPOSED";
  const rejected = check.status === "REJECTED";
  const failing = result && ["FAIL", "ERROR"].includes(result.outcome);

  useEffect(() => { setEditing(null); }, [check.status]);

  const decide = (decision: "APPROVED" | "REJECTED" | "MODIFIED") => start(async () => {
    setError("");
    const need = (decision === "MODIFIED" ? editing?.requirement : check.client_requirement)?.trim() || "";
    if (decision === "MODIFIED" && !need) { setError("Add the business need so the change stays traceable."); return; }
    const r = await saveSodaDecisions(runId, [{
      expectation_id: check.expectation_id, decision, justification: why.trim() || undefined,
      requirement: need || check.client_requirement || undefined,
      ...(decision === "MODIFIED" && editing ? { definition: cleanDefinition(editing.definition), severity: editing.severity } : {}),
    }]);
    if (!r.ok) { setError(r.error); return; }
    setEditing(null);
  });

  return (
    <div className={cn("rounded-xl border bg-card transition",
                       failing ? "border-destructive/40" : proposed ? "border-warning/40" : "",
                       rejected && "opacity-60")}>
      <div className="flex flex-wrap items-start gap-3 p-3.5">
        <div className="min-w-0 flex-1 space-y-1">
          <p className={cn("text-[15px] font-medium leading-snug", rejected && "line-through")}>{describeCheck(check)}</p>
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span className="text-muted-foreground">{kindLabel(check)}</span>
            <Badge variant={check.severity === "FAIL" ? "destructive" : "outline"} className="px-1.5 py-0 text-[10px]">{check.severity === "FAIL" ? "fail" : "warn"}</Badge>
            <Badge variant={proposed ? "warning" : rejected ? "outline" : "success"} className="px-1.5 py-0 text-[10px]">{check.status.toLowerCase()}</Badge>
            <span className="text-muted-foreground">from {check.origin.toLowerCase()}</span>
          </div>
          {check.client_requirement && <p className="truncate text-xs text-muted-foreground" title={check.client_requirement}>{check.client_requirement}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="flex items-center gap-2">
            <Spark points={history} />
            <OutcomeBadge outcome={result?.outcome} />
          </span>
          {result && result.measured != null && (
            <span className="font-mono text-[11px] text-muted-foreground">
              {fmtNumber(result.measured)}{result.threshold ? ` vs ${result.threshold}` : ""}{result.failed_rows ? ` · ${fmtNumber(result.failed_rows)} rows` : ""}
            </span>
          )}
          {!result && check.backtest && check.backtest.status !== "NOT_EVALUATED" && (
            <span className={cn("text-[11px]", check.backtest.status === "FAIL" ? "text-destructive" : "text-success")} title={check.backtest.detail}>
              backtest {check.backtest.status.toLowerCase()}
            </span>
          )}
        </div>
      </div>

      {editing && (
        <div className="space-y-3 border-t bg-muted/20 p-3.5">
          <CheckEditor draft={editing} onChange={setEditing} columns={columnNames} kindLocked />
          <Textarea rows={2} value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why it changed (optional)" aria-label="Justification" />
          <div className="flex gap-2">
            <Button size="sm" disabled={pending} onClick={() => decide("MODIFIED")}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save & approve</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1 border-t px-2.5 py-1.5">
        <button type="button" onClick={() => setDetails((v) => !v)} aria-expanded={details}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-xs text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", details && "rotate-180")} />Details
        </button>
        {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
        {canReview && proposed && !editing && (
          <div className="ml-auto flex gap-1">
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => { setEditing(draftFrom(check)); setDetails(false); }}>
              <Pencil className="h-3.5 w-3.5" />Edit
            </Button>
            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" disabled={pending} onClick={() => decide("REJECTED")}>
              <X className="h-3.5 w-3.5" />Reject
            </Button>
            <Button size="sm" disabled={pending} onClick={() => decide("APPROVED")}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckIcon className="h-3.5 w-3.5" />}Approve
            </Button>
          </div>
        )}
      </div>

      {details && (
        <div className="space-y-3 border-t p-3.5 text-xs">
          {result?.detail && <p><span className="font-semibold">Last scan: </span>{result.detail}</p>}
          {check.evidence && <p><span className="font-semibold">Why this check: </span>{check.evidence}</p>}
          {check.backtest?.detail && <p><span className="font-semibold">Backtest: </span>{check.backtest.detail}</p>}
          {check.sodacl && (
            <div>
              <p className="mb-1 font-semibold">SodaCL</p>
              <pre className="overflow-auto rounded-md bg-muted/50 p-2 font-mono leading-relaxed">{check.sodacl}</pre>
            </div>
          )}
          {result?.sample && result.sample.length > 0 && (
            <div>
              <p className="mb-1 font-semibold">Failed rows ({result.sample.length}, PII masked)</p>
              <div className="max-h-48 overflow-auto rounded-md border">
                <table className="w-full">
                  <thead className="sticky top-0 bg-muted"><tr>{Object.keys(result.sample[0]).map((k) => <th key={k} className="px-2 py-1 text-left font-mono font-medium">{k}</th>)}</tr></thead>
                  <tbody>
                    {result.sample.map((row, i) => (
                      <tr key={i} className="border-t">{Object.keys(result.sample![0]).map((k) => <td key={k} className="whitespace-nowrap px-2 py-1 font-mono">{row[k] == null ? "NULL" : String(row[k])}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {result?.sql_text && (
            <div>
              <p className="mb-1 font-semibold">SQL executed</p>
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono leading-relaxed">{result.sql_text}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
