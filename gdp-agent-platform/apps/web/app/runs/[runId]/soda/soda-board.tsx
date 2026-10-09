"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { addSodaCheck, backtestSoda, importSoda, saveSodaDecisions } from "../pipeline-actions";
import type { SttmLine } from "../sttm/sttm-board";
import { CheckEditor, cleanDefinition, draftFrom, type Draft } from "./check-editor";
import { OutcomeBadge, Spark, type CheckResult, type HistoryPoint } from "./quality-shared";

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

function BacktestBadge({ result }: { result?: Check["backtest"] }) {
  if (!result) return <span className="text-xs text-muted-foreground">—</span>;
  if (result.status === "NOT_EVALUATED") return <Badge variant="outline" title={result.detail}>n/a</Badge>;
  return <Badge variant={result.status === "PASS" ? "success" : "destructive"} title={result.detail}>{result.status.toLowerCase()}</Badge>;
}

type MassKind = "APPROVED" | "REJECTED";

const ALL = "ALL";

function badgeForStatus(status: string) {
  if (status === "APPROVED") return "success" as const;
  if (status === "REJECTED") return "destructive" as const;
  return "warning" as const;
}

function unique(values: (string | null | undefined)[]) {
  return Array.from(new Set(values.map((v) => (v || "").trim()).filter(Boolean))).sort();
}

function matches(check: Check, query: string, status: string, severity: string, origin: string, table: string) {
  if (status !== ALL && check.status !== status) return false;
  if (severity !== ALL && check.severity !== severity) return false;
  if (origin !== ALL && check.origin !== origin) return false;
  if (table !== ALL && check.target_table !== table) return false;
  if (!query) return true;
  const hay = [
    check.target_table, check.target_column, check.check_type, check.origin,
    check.status, check.severity, check.client_requirement, check.sodacl,
  ].join(" ").toLowerCase();
  return hay.includes(query);
}

function proposedOf(checks: Check[], ids: Set<string>) {
  return checks.filter((c) => ids.has(c.expectation_id) && c.status === "PROPOSED");
}

export function SodaBoard({
  runId, checks, yaml, gxSuite, brief, sttmLines = [], canImport, canReview, latest = [], history = {}, columns = [],
}: {
  latest?: CheckResult[];
  history?: Record<string, HistoryPoint[]>;
  columns?: string[];
  runId: string;
  checks: Check[];
  yaml: string;
  gxSuite?: Record<string, unknown> | null;
  brief: { title: string; content: string } | null;
  sttmLines?: SttmLine[];
  canImport: boolean;
  canReview: boolean;
}) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState(checks.find((c) => c.status === "PROPOSED")?.expectation_id ?? checks[0]?.expectation_id ?? "");
  const [justification, setJustification] = useState("");
  const [requirement, setRequirement] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState(ALL);
  const [severityFilter, setSeverityFilter] = useState(ALL);
  const [originFilter, setOriginFilter] = useState(ALL);
  const [tableFilter, setTableFilter] = useState(ALL);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mass, setMass] = useState<MassKind | null>(null);
  const [massNote, setMassNote] = useState("Checks match the approved STTM and the client quality need.");
  const [showYaml, setShowYaml] = useState(false);
  const [outputTab, setOutputTab] = useState<"soda" | "gx">("soda");
  const [showCoverage, setShowCoverage] = useState(true);
  const [groupByColumn, setGroupByColumn] = useState(false);
  const [columnFocus, setColumnFocus] = useState("");
  const [editing, setEditing] = useState<Draft | null>(null);
  const [adding, setAdding] = useState<Draft | null>(null);
  const [pending, start] = useTransition();
  const [testing, startTest] = useTransition();
  const lastResult = useMemo(() => new Map(latest.map((r) => [r.expectation_id, r])), [latest]);
  const columnNames = useMemo(
    () => Array.from(new Set([...columns, ...sttmLines.map((l) => l.target_column.toUpperCase())])).sort(),
    [columns, sttmLines],
  );
  const lastIndex = useRef<number>(-1);
  const headerBox = useRef<HTMLInputElement>(null);

  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () => checks.filter((c) => {
      if (!matches(c, q, statusFilter, severityFilter, originFilter, tableFilter)) return false;
      if (columnFocus && (c.target_column || "").toUpperCase() !== columnFocus.toUpperCase()) return false;
      return true;
    }),
    [checks, q, statusFilter, severityFilter, originFilter, tableFilter, columnFocus],
  );
  const current = checks.find((c) => c.expectation_id === open) ?? visible[0] ?? checks[0];
  const proposed = checks.filter((c) => c.status === "PROPOSED");
  const visibleProposed = visible.filter((c) => c.status === "PROPOSED");
  const selectedProposed = proposedOf(checks, selected);
  const failInSelection = selectedProposed.filter((c) => c.severity === "FAIL").length;
  const allVisibleSelected = visibleProposed.length > 0 && visibleProposed.every((c) => selected.has(c.expectation_id));
  const someVisibleSelected = visibleProposed.some((c) => selected.has(c.expectation_id));

  useEffect(() => {
    const keep = new Set(checks.map((c) => c.expectation_id));
    setSelected((prev) => {
      const next = new Set(Array.from(prev).filter((id) => keep.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [checks]);

  useEffect(() => {
    if (headerBox.current) headerBox.current.indeterminate = someVisibleSelected && !allVisibleSelected;
  }, [someVisibleSelected, allVisibleSelected]);

  const tables = unique(checks.map((c) => c.target_table));
  const origins = unique(checks.map((c) => c.origin));
  const severities = unique(checks.map((c) => c.severity));
  const byColumn = useMemo(() => {
    const map = new Map<string, Check[]>();
    for (const c of checks) {
      const key = (c.target_column || c.target_table || "").toUpperCase();
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return map;
  }, [checks]);
  const coverage = useMemo(() => {
    const rows = sttmLines.map((line) => {
      const related = byColumn.get(line.target_column.toUpperCase()) ?? [];
      return {
        column: line.target_column,
        source: [line.source_table, line.source_column].filter(Boolean).join("."),
        mapping: line.mapping_type,
        checks: related.length,
        proposed: related.filter((c) => c.status === "PROPOSED").length,
      };
    });
    const uncovered = rows.filter((r) => r.checks === 0 && r.mapping !== "UNMAPPED");
    return { rows, uncovered, covered: rows.length - uncovered.length };
  }, [sttmLines, byColumn]);
  const currentLine = current
    ? sttmLines.find((l) => l.target_column.toUpperCase() === (current.target_column || "").toUpperCase())
    : undefined;

  const toggleRow = (id: string, index: number, shift: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (shift && lastIndex.current >= 0) {
        const [a, b] = lastIndex.current < index
          ? [lastIndex.current, index]
          : [index, lastIndex.current];
        for (let i = a; i <= b; i += 1) {
          const row = visible[i];
          if (row?.status === "PROPOSED") next.add(row.expectation_id);
        }
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    lastIndex.current = index;
  };

  const toggleVisibleProposed = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        visibleProposed.forEach((c) => next.delete(c.expectation_id));
      } else {
        visibleProposed.forEach((c) => next.add(c.expectation_id));
      }
      return next;
    });
  };

  const beginMass = (kind: MassKind, ids?: string[]) => {
    setError("");
    setNotice("");
    const target = ids ? new Set(ids) : selected;
    const ready = proposedOf(checks, target);
    if (ready.length === 0) {
      setError(ids ? "There are no remaining proposed checks to decide." : "Select at least one proposed check.");
      setMass(null);
      return;
    }
    setSelected(new Set(ready.map((c) => c.expectation_id)));
    setMass(kind);
  };

  const apply = (rows: { expectation_id: string; decision: MassKind | "MODIFIED"; justification?: string; requirement?: string }[]) => {
    if (rows.length === 0) {
      setError("Nothing to apply.");
      return;
    }
    start(async () => {
      setError("");
      setNotice("");
      const result = await saveSodaDecisions(runId, rows);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      const skipped = result.data.skipped?.length ?? 0;
      setNotice(`${result.data.decided} applied${skipped ? ` · ${skipped} already decided, skipped` : ""}${result.data.complete ? " · pack is ready to approve" : ` · ${result.data.remaining.length} still proposed`}`);
      setMass(null);
      setSelected(new Set());
      setJustification("");
    });
  };

  const confirmMass = () => {
    const note = massNote.trim();
    if (note.length < 8) {
      setError("Mass actions need a business justification of at least 8 characters.");
      return;
    }
    if (!mass) return;
    apply(selectedProposed.map((c) => ({
      expectation_id: c.expectation_id,
      decision: mass,
      justification: note,
      requirement: c.client_requirement || undefined,
    })));
  };

  const decideOne = (decision: "APPROVED" | "REJECTED" | "MODIFIED") => {
    if (!current || current.status !== "PROPOSED") {
      setError("Only proposed checks can be decided.");
      return;
    }
    if (decision === "MODIFIED" && !editing) {
      setEditing(draftFrom(current));
      setError("");
      return;
    }
    const need = (decision === "MODIFIED" ? editing?.requirement : requirement)?.trim() || current.client_requirement || "";
    if (decision === "MODIFIED" && !need) {
      setError("Modify needs a business need so the check stays traceable.");
      return;
    }
    apply([{
      expectation_id: current.expectation_id,
      decision,
      justification: justification.trim() || undefined,
      requirement: need || undefined,
      ...(decision === "MODIFIED" && editing
        ? { definition: cleanDefinition(editing.definition), severity: editing.severity }
        : {}),
    }]);
    setEditing(null);
  };

  const saveNew = () => start(async () => {
    if (!adding) return;
    setError("");
    setNotice("");
    const result = await addSodaCheck(runId, {
      target_column: adding.target_column || null,
      severity: adding.severity,
      requirement: adding.requirement,
      definition: cleanDefinition(adding.definition),
    });
    if (!result.ok) { setError(result.error); return; }
    setNotice("Check added as proposed. Review and approve it like any other check.");
    setAdding(null);
    setOpen(result.data.expectation_id);
  });

  const lineFor = (check: Check) =>
    sttmLines.find((l) => l.target_column.toUpperCase() === (check.target_column || "").toUpperCase());

  const download = (content: string, name: string, type: string) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };
  const gxText = gxSuite ? JSON.stringify(gxSuite, null, 2) : "";
  const gxCount = Array.isArray((gxSuite as { expectations?: unknown[] } | null)?.expectations)
    ? (gxSuite as { expectations: unknown[] }).expectations.length : 0;
  const tested = checks.filter((c) => c.backtest && c.backtest.status !== "NOT_EVALUATED");
  const failing = tested.filter((c) => c.backtest?.status === "FAIL");

  const runBacktest = () => startTest(async () => {
    setError("");
    setNotice("");
    const result = await backtestSoda(runId);
    if (!result.ok) { setError(result.error); return; }
    const s = result.data.summary;
    setNotice(`Backtest on today's source data: ${s.PASS} pass · ${s.FAIL} fail · ${s.NOT_EVALUATED} only checkable on the built model`);
  });

  const groups = useMemo(() => {
    const map = new Map<string, Check[]>();
    for (const c of visible) {
      const key = c.target_column || c.target_table || "(table)";
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [visible]);

  const upload = () => start(async () => {
    setError("");
    setNotice("");
    if (!text.trim()) {
      setError("Upload a file or paste the client quality brief first.");
      return;
    }
    const result = await importSoda(runId, { brief: text, filename });
    if (!result.ok) setError(result.error);
  });

  return (
    <div className="space-y-5">
      {canImport && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Upload the client quality pack — CSV, JSON, Markdown, or pasted notes.
            Cortex extracts SodaCL checks against the approved STTM. Confirm each
            check against the business need, or mass-approve the proposed set.
          </p>
          <Label htmlFor="soda_file">Client brief</Label>
          <input
            id="soda_file"
            type="file"
            accept=".csv,.json,.txt,.md,.yml,.yaml"
            className="block text-sm"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setFilename(file.name);
              const reader = new FileReader();
              reader.onload = () => setText(String(reader.result || ""));
              reader.readAsText(file);
            }}
          />
          <Textarea
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste requirements, SLAs, or accepted-value lists. Example: EMAIL must be a valid email; CUSTOMER_STATUS in ACTIVE, INACTIVE; load must be fresher than 1 day."
          />
          <Button type="button" disabled={pending} onClick={upload}>
            {pending && <Loader2 className="h-4 w-4 animate-spin" />}
            {pending ? "Extracting checks…" : "Extract checks from brief"}
          </Button>
        </div>
      )}

      {brief && (
        <div className="rounded-lg border bg-muted/40 p-3 text-sm">
          <p className="font-medium">{brief.title}</p>
          <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{brief.content.slice(0, 800)}</p>
        </div>
      )}

      {checks.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant="outline">{checks.length} checks</Badge>
          <Badge variant="warning">{proposed.length} proposed</Badge>
          <Badge variant="success">{checks.filter((c) => c.status === "APPROVED").length} approved</Badge>
          <Badge variant="destructive">{checks.filter((c) => c.status === "REJECTED").length} rejected</Badge>
          <Badge variant="outline">{checks.filter((c) => c.severity === "FAIL").length} FAIL severity</Badge>
          <Badge variant="outline">{checks.filter((c) => c.origin === "PROFILE").length} from the data profile</Badge>
          {tested.length > 0 && (
            <Badge variant={failing.length ? "destructive" : "success"}>
              backtest {tested.length - failing.length}/{tested.length} pass
            </Badge>
          )}
          {sttmLines.length > 0 && (
            <Badge variant={coverage.uncovered.length ? "warning" : "success"}>
              {coverage.covered}/{coverage.rows.length} STTM columns covered
            </Badge>
          )}
        </div>
      )}

      {sttmLines.length > 0 && (
        <div className="space-y-2 rounded-lg border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium">STTM → check lineage</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={() => setShowCoverage((v) => !v)}>
                {showCoverage ? "Hide coverage" : "Show coverage"}
              </Button>
              <Button type="button" size="sm" variant={groupByColumn ? "default" : "outline"} onClick={() => setGroupByColumn((v) => !v)}>
                Group by column
              </Button>
              {columnFocus && (
                <Button type="button" size="sm" variant="ghost" onClick={() => setColumnFocus("")}>
                  Clear column focus
                </Button>
              )}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Each check traces to a target column on the approved STTM. Uncovered mapped columns still need a check.
            {columnFocus ? ` Focusing ${columnFocus}.` : ""}
          </p>
          {showCoverage && (
            <Table>
              <THead>
                <TR>
                  <TH>Source</TH>
                  <TH>STTM target</TH>
                  <TH>Mapping</TH>
                  <TH>Checks</TH>
                </TR>
              </THead>
              <TBody>
                {coverage.rows.map((row) => (
                  <TR key={row.column}>
                    <TD className="font-mono text-xs">{row.source || "—"}</TD>
                    <TD>
                      <button
                        type="button"
                        className="font-mono text-xs hover:underline"
                        onClick={() => setColumnFocus(row.column)}
                      >
                        {row.column}
                      </button>
                    </TD>
                    <TD><Badge variant="outline">{row.mapping}</Badge></TD>
                    <TD>
                      {row.checks === 0
                        ? <Badge variant={row.mapping === "UNMAPPED" ? "outline" : "warning"}>none</Badge>
                        : <Badge variant={row.proposed ? "warning" : "success"}>{row.checks}{row.proposed ? ` · ${row.proposed} open` : ""}</Badge>}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </div>
      )}

      {checks.length > 0 && (
        <div className="space-y-3 rounded-lg border p-3">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
            <Input
              aria-label="Search checks"
              placeholder="Search column, type, SodaCL…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Select aria-label="Status filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value={ALL}>All statuses</option>
              <option value="PROPOSED">Proposed</option>
              <option value="APPROVED">Approved</option>
              <option value="REJECTED">Rejected</option>
            </Select>
            <Select aria-label="Severity filter" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option value={ALL}>All severities</option>
              {severities.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
            <Select aria-label="Origin filter" value={originFilter} onChange={(e) => setOriginFilter(e.target.value)}>
              <option value={ALL}>All origins</option>
              {origins.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
            <Select aria-label="Table filter" value={tableFilter} onChange={(e) => setTableFilter(e.target.value)}>
              <option value={ALL}>All tables</option>
              {tables.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              Showing {visible.length} of {checks.length}. Shift-click to select a range of proposed rows.
            </p>
            {canReview && (
              <Button type="button" size="sm" variant={adding ? "secondary" : "outline"}
                      onClick={() => setAdding(adding ? null : { target_column: "", severity: "FAIL", requirement: "", definition: { kind: "failed_rows" } })}>
                {adding ? "Close new check" : "Add check"}
              </Button>
            )}
          </div>
          {adding && (
            <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm font-medium">New check</p>
              <CheckEditor draft={adding} onChange={setAdding} columns={columnNames} />
              <div className="flex gap-2">
                <Button type="button" size="sm" disabled={pending} onClick={saveNew}>
                  {pending && <Loader2 className="h-4 w-4 animate-spin" />}Add as proposed
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(null)}>Cancel</Button>
              </div>
            </div>
          )}
          {canReview && (
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" disabled={pending || selectedProposed.length === 0} onClick={() => beginMass("APPROVED")}>
                Approve selected ({selectedProposed.length})
              </Button>
              <Button type="button" size="sm" variant="destructive" disabled={pending || selectedProposed.length === 0} onClick={() => beginMass("REJECTED")}>
                Reject selected ({selectedProposed.length})
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={pending || proposed.length === 0} onClick={() => beginMass("APPROVED", proposed.map((c) => c.expectation_id))}>
                Approve all remaining ({proposed.length})
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={pending || selected.size === 0} onClick={() => { setSelected(new Set()); setMass(null); }}>
                Clear selection
              </Button>
              <Button type="button" size="sm" variant="outline" className="ml-auto" disabled={pending || testing} onClick={runBacktest}
                      title="Evaluate every check on the current source data before accepting it">
                {testing && <Loader2 className="h-4 w-4 animate-spin" />}
                {testing ? "Backtesting…" : "Backtest on source data"}
              </Button>
            </div>
          )}
        </div>
      )}

      {mass && canReview && (
        <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3">
          <p className="text-sm font-medium">
            {mass === "APPROVED" ? "Mass approve" : "Mass reject"} {selectedProposed.length} proposed check{selectedProposed.length === 1 ? "" : "s"}
            {failInSelection > 0 ? ` · ${failInSelection} FAIL severity` : ""}
          </p>
          <p className="text-xs text-muted-foreground">
            Already decided rows are skipped. This does not approve the Data Quality pack — that is a separate gate.
          </p>
          <Label htmlFor="mass_why">Business justification</Label>
          <Textarea id="mass_why" rows={2} value={massNote} onChange={(e) => setMassNote(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant={mass === "REJECTED" ? "destructive" : "default"} disabled={pending || massNote.trim().length < 8} onClick={confirmMass}>
              {pending && <Loader2 className="h-4 w-4 animate-spin" />}
              {pending ? "Applying…" : `Confirm ${mass === "APPROVED" ? "approve" : "reject"}`}
            </Button>
            <Button type="button" variant="outline" disabled={pending} onClick={() => setMass(null)}>Cancel</Button>
          </div>
        </div>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}

      {checks.length === 0 && (
        <p className="text-sm text-muted-foreground">No checks yet. Generate from the STTM or extract them from a client brief.</p>
      )}

      {visible.length === 0 && checks.length > 0 && (
        <p className="text-sm text-muted-foreground">No checks match the current filters.</p>
      )}

      {visible.length > 0 && (
        <Table>
          <THead>
            <TR>
              <TH className="w-10">
                {canReview ? (
                  <input
                    ref={headerBox}
                    type="checkbox"
                    aria-label="Select all visible proposed checks"
                    checked={allVisibleSelected}
                    disabled={pending || visibleProposed.length === 0}
                    onChange={toggleVisibleProposed}
                  />
                ) : null}
              </TH>
              <TH>STTM source</TH>
              <TH>Column</TH>
              <TH>Type</TH>
              <TH>Severity</TH>
              <TH>Origin</TH>
              <TH>{latest.length ? "Last scan" : "Backtest"}</TH>
              <TH>Status</TH>
            </TR>
          </THead>
          <TBody>
            {(groupByColumn ? groups.flatMap(([col, rows]) => [
              { kind: "head" as const, col, rows },
              ...rows.map((c, i) => ({ kind: "row" as const, c, index: visible.indexOf(c), col })),
            ]) : visible.map((c, index) => ({ kind: "row" as const, c, index, col: "" }))).map((item) => (
              item.kind === "head" ? (
                <TR key={`g-${item.col}`}>
                  <TD colSpan={8} className="bg-muted/50 text-xs font-semibold">
                    {item.col} · {item.rows.length} check{item.rows.length === 1 ? "" : "s"}
                  </TD>
                </TR>
              ) : (
              <TR
                key={item.c.expectation_id}
                className={item.c.expectation_id === current?.expectation_id ? "bg-accent/60" : undefined}
              >
                <TD>
                  {canReview && item.c.status === "PROPOSED" ? (
                    <input
                      type="checkbox"
                      aria-label={`Select ${item.c.target_column || item.c.target_table} ${item.c.check_type}`}
                      checked={selected.has(item.c.expectation_id)}
                      disabled={pending}
                      onChange={() => undefined}
                      onClick={(e) => {
                        e.preventDefault();
                        toggleRow(item.c.expectation_id, item.index, e.shiftKey);
                      }}
                    />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TD>
                <TD className="font-mono text-xs text-muted-foreground">
                  {(() => {
                    const line = lineFor(item.c);
                    return line ? [line.source_table, line.source_column].filter(Boolean).join(".") || "—" : "—";
                  })()}
                </TD>
                <TD>
                  <button
                    type="button"
                    className="text-left font-mono text-xs hover:underline"
                    onClick={() => {
                      setOpen(item.c.expectation_id);
                      setEditing(null);
                      setJustification("");
                      setRequirement(item.c.client_requirement || "");
                    }}
                  >
                    {item.c.target_table}{item.c.target_column ? `.${item.c.target_column}` : ""}
                  </button>
                </TD>
                <TD>{item.c.check_type}</TD>
                <TD>
                  <Badge variant={item.c.severity === "FAIL" ? "destructive" : "outline"}>{item.c.severity}</Badge>
                </TD>
                <TD>{item.c.origin}</TD>
                <TD>
                  {latest.length ? (
                    <span className="flex items-center gap-2">
                      <OutcomeBadge outcome={lastResult.get(item.c.expectation_id)?.outcome} />
                      <Spark points={history[item.c.expectation_id] ?? []} />
                    </span>
                  ) : <BacktestBadge result={item.c.backtest} />}
                </TD>
                <TD><Badge variant={badgeForStatus(item.c.status)}>{item.c.status}</Badge></TD>
              </TR>
              )
            ))}
          </TBody>
        </Table>
      )}

      {current && (
        <div className="space-y-3 rounded-lg border p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3>{current.check_type}</h3>
            <Badge variant={current.severity === "FAIL" ? "destructive" : "outline"}>{current.severity}</Badge>
            <Badge variant="outline">{current.origin}</Badge>
            <Badge variant={badgeForStatus(current.status)}>{current.status}</Badge>
          </div>
          {currentLine && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs">
              <span className="font-semibold uppercase tracking-wide text-muted-foreground">Lineage</span>
              <span className="font-mono">{[currentLine.source_table, currentLine.source_column].filter(Boolean).join(".") || "derived"}</span>
              <span aria-hidden className="text-muted-foreground">→</span>
              <span className="font-mono">{current.target_table}.{currentLine.target_column}</span>
              <span aria-hidden className="text-muted-foreground">→</span>
              <span className="font-medium">{current.check_type}</span>
              <Badge variant="outline">{currentLine.mapping_type}</Badge>
              {currentLine.transformation && (
                <span className="basis-full font-mono text-muted-foreground">{currentLine.transformation}</span>
              )}
            </div>
          )}
          <p className="text-sm">{current.client_requirement || "Derived from the STTM and SodaCL defaults."}</p>
          {current.evidence && (
            <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs">
              <span className="font-semibold">Evidence: </span>{current.evidence}
            </div>
          )}
          {current.backtest && (
            <div className={`rounded-md border px-3 py-2 text-xs ${current.backtest.status === "FAIL" ? "border-destructive/40 bg-destructive/5" : "bg-muted/30"}`}>
              <span className="font-semibold">Backtest: </span>
              {current.backtest.status === "NOT_EVALUATED" ? "not evaluable on source data; " : `${current.backtest.status.toLowerCase()}; `}
              {current.backtest.detail}
            </div>
          )}
          <p className="font-mono text-xs text-muted-foreground">
            {current.target_table}{current.target_column ? `.${current.target_column}` : ""}
          </p>
          {lastResult.get(current.expectation_id) && (() => {
            const r = lastResult.get(current.expectation_id)!;
            return (
              <div className={`space-y-1 rounded-md border px-3 py-2 text-xs ${r.outcome === "FAIL" || r.outcome === "ERROR" ? "border-destructive/40 bg-destructive/5" : r.outcome === "WARN" ? "border-warning/40 bg-warning/5" : "bg-muted/30"}`}>
                <div className="flex items-center gap-2">
                  <span className="font-semibold">Last scan</span>
                  <OutcomeBadge outcome={r.outcome} />
                  <Spark points={history[current.expectation_id] ?? []} />
                </div>
                <p>{r.detail}</p>
              </div>
            );
          })()}
          {current.sodacl && (
            <pre className="overflow-auto rounded-md bg-muted/40 p-2 font-mono text-xs leading-relaxed">{current.sodacl}</pre>
          )}
          {canReview && current.status === "PROPOSED" && editing && (
            <div className="space-y-3 rounded-md border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm font-medium">Modify, then approve</p>
              <CheckEditor draft={editing} onChange={setEditing} columns={columnNames} kindLocked />
              <Label htmlFor="soda_why_edit">Justification</Label>
              <Textarea id="soda_why_edit" rows={2} value={justification} onChange={(e) => setJustification(e.target.value)}
                        placeholder="Why the threshold or values changed" />
              <div className="flex gap-2">
                <Button type="button" disabled={pending} onClick={() => decideOne("MODIFIED")}>Save & approve</Button>
                <Button type="button" variant="ghost" disabled={pending} onClick={() => setEditing(null)}>Cancel</Button>
              </div>
            </div>
          )}
          {canReview && current.status === "PROPOSED" && !editing && (
            <>
              <Label htmlFor="soda_need">Business need</Label>
              <Textarea id="soda_need" rows={2} value={requirement} onChange={(e) => setRequirement(e.target.value)} placeholder="What the client asked this check to protect" />
              <Label htmlFor="soda_why">Justification</Label>
              <Textarea id="soda_why" rows={2} value={justification} onChange={(e) => setJustification(e.target.value)} placeholder="Why this check stays, changes, or is dropped" />
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={pending} onClick={() => decideOne("APPROVED")}>Approve</Button>
                <Button type="button" variant="outline" disabled={pending} onClick={() => decideOne("MODIFIED")}>Modify & approve</Button>
                <Button type="button" variant="destructive" disabled={pending} onClick={() => decideOne("REJECTED")}>Reject</Button>
              </div>
            </>
          )}
          {canReview && current.status !== "PROPOSED" && (
            <p className="text-sm text-muted-foreground">This check is already {current.status.toLowerCase()}. Mass actions only apply to proposed rows.</p>
          )}
        </div>
      )}

      {yaml && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={() => setShowYaml((v) => !v)}>
            {showYaml ? "Hide compiled checks" : "Show compiled checks"}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => download(yaml, "data-quality.soda.yml", "text/yaml")}>
            Download SodaCL
          </Button>
          {gxText && (
            <Button type="button" size="sm" variant="outline"
                    onClick={() => download(gxText, "data-quality.gx-suite.json", "application/json")}>
              Download Great Expectations suite
            </Button>
          )}
        </div>
      )}
      {yaml && showYaml && (
        <div className="space-y-2">
          <div role="tablist" className="flex gap-1">
            {([["soda", "SodaCL"], ["gx", `Great Expectations (${gxCount})`]] as const).map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={outputTab === key}
                      disabled={key === "gx" && !gxText} onClick={() => setOutputTab(key)}
                      className={`rounded-md px-3 py-1 text-xs font-medium ${outputTab === key ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                {label}
              </button>
            ))}
          </div>
          <pre className="max-h-[520px] overflow-auto rounded-lg border bg-muted/30 p-3 text-xs leading-relaxed">
            {outputTab === "soda" ? yaml : gxText}
          </pre>
        </div>
      )}
    </div>
  );
}
