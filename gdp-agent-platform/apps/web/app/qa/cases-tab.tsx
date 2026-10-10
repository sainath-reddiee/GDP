"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { ChevronDown, LifeBuoy, Loader2, Plus, RefreshCw, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { ReportDialog } from "@/components/report-problem";
import { cn } from "@/lib/utils";
import { When } from "../ops/ops-shared";
import { SeverityPill } from "../incidents/incident-shared";
import { caseSummary, listCases, type CaseRow, type CaseSummary } from "./cases/actions";
import { DEFAULT_FILTERS, KINDS, OPEN_STATUSES, SEVERITY_IDS, STATUSES, type CaseFilterState } from "./cases/case-filters";
import { caseHref, CaseKindPill, CaseStatusPill, caseStatusLabel, SourceChip } from "./cases/case-ui";
import { Alert, Empty, plural, useLive, useSeq, type Access } from "./qa-shared";

const PAGE = 50;
const sameSet = (a: string[], b: readonly string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** The case queue: summary chips, filters kept in the URL, and a table of cases in the caller's domains. */
export function CasesTab({ filters, setFilters, access, domains }: {
  filters: CaseFilterState; setFilters: (f: CaseFilterState) => void; access: Access; domains: { domain_id: string; name: string }[];
}) {
  const [cases, setCases] = useState<CaseRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [error, setError] = useState("");
  const [summaryError, setSummaryError] = useState("");
  const [text, setText] = useState(filters.q);
  const [reporting, setReporting] = useState(false);
  const [reload, setReload] = useState(0);
  const [loading, startLoad] = useTransition();
  const [paging, startPage] = useTransition();
  const seq = useSeq();
  const sumSeq = useSeq();
  const live = useLive();

  const query = (offset: number) => listCases({
    status: filters.status, severity: filters.severity, kind: filters.kind, domain_id: filters.domain, mine: filters.mine,
    q: filters.q, sla: filters.breached ? "breached" : "", limit: PAGE, offset,
  });

  useEffect(() => {
    const n = seq.next();
    setCases(null); setError("");
    startLoad(async () => {
      try {
        const r = await query(0);
        if (!seq.current(n) || !live.current) return;
        if (r.ok) { setCases(r.data.cases); setTotal(r.data.total); } else { setCases([]); setTotal(0); setError(r.error); }
      } catch (e) {
        if (seq.current(n) && live.current) { setCases([]); setError(e instanceof Error ? e.message : "Could not load the cases."); }
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, reload]);

  useEffect(() => {
    const n = sumSeq.next();
    caseSummary().then((r) => {
      if (!sumSeq.current(n) || !live.current) return;
      if (r.ok) { setSummary(r.data); setSummaryError(""); } else setSummaryError(r.error);
    }, (e: unknown) => { if (sumSeq.current(n) && live.current) setSummaryError(e instanceof Error ? e.message : "Could not load the summary."); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  const more = () => {
    const n = seq.next();
    const offset = cases?.length ?? 0;
    startPage(async () => {
      try {
        const r = await query(offset);
        if (!seq.current(n) || !live.current) return;
        if (!r.ok) { setError(r.error); return; }
        setCases((list) => {
          const have = new Set((list ?? []).map((c) => c.case_id));
          return [...(list ?? []), ...r.data.cases.filter((c) => !have.has(c.case_id))];
        });
        setTotal(r.data.total);
      } catch (e) {
        if (seq.current(n) && live.current) setError(e instanceof Error ? e.message : "Could not load more cases.");
      }
    });
  };

  const set = (patch: Partial<CaseFilterState>) => setFilters({ ...filters, ...patch });
  const filtered = !sameSet(filters.status, OPEN_STATUSES) || !!filters.severity || !!filters.kind || !!filters.domain
    || filters.mine || !!filters.q || filters.breached;
  const chip = (label: string, value: number | undefined, active: boolean, apply: () => void, tone?: string) => (
    <button type="button" onClick={apply} aria-pressed={active}
            className={cn("flex items-baseline gap-1.5 rounded-xl border bg-card px-3 py-2 text-left shadow-sm transition hover:border-primary/40",
              active && "border-primary ring-1 ring-primary/30")}>
      <span className={cn("text-lg font-semibold tabular-nums", tone)}>{value ?? "-"}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {chip("open", summary?.open, sameSet(filters.status, OPEN_STATUSES) && !filters.mine && !filters.severity && !filters.breached,
          () => { setText(""); setFilters({ ...DEFAULT_FILTERS, domain: filters.domain }); })}
        {chip("mine", summary?.mine, filters.mine, () => set({ mine: !filters.mine }))}
        {chip("P1 open", summary?.p1_open, filters.severity === "P1", () => set({ severity: filters.severity === "P1" ? "" : "P1" }),
          summary?.p1_open ? "text-rose-700" : undefined)}
        {chip("SLA breached", summary?.sla_breached, filters.breached, () => set({ breached: !filters.breached }),
          summary?.sla_breached ? "text-rose-700" : undefined)}
        <span className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" disabled={loading} onClick={() => setReload((n) => n + 1)} aria-label="Reload">
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Reload</Button>
          {access.canCase && <Button size="sm" onClick={() => setReporting(true)}><Plus className="h-3.5 w-3.5" />New case</Button>}
        </span>
      </div>
      {summaryError && <Alert onDismiss={() => setSummaryError("")}>Summary: {summaryError}</Alert>}

      <div className="flex flex-wrap items-center gap-2">
        <StatusPicker value={filters.status} onChange={(status) => set({ status })} />
        <Select value={filters.severity} onChange={(e) => set({ severity: e.target.value })} className="h-8 w-auto text-xs" aria-label="Severity">
          <option value="">Any severity</option>
          {SEVERITY_IDS.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select value={filters.kind} onChange={(e) => set({ kind: e.target.value })} className="h-8 w-auto text-xs" aria-label="Kind">
          <option value="">Any kind</option>
          {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
        </Select>
        <Select value={filters.domain} onChange={(e) => set({ domain: e.target.value })} className="h-8 w-auto max-w-[14rem] text-xs" aria-label="Domain">
          <option value="">All my domains</option>
          {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.name}</option>)}
        </Select>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={filters.mine} onChange={() => set({ mine: !filters.mine })} />Assigned to me</label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={filters.breached} onChange={() => set({ breached: !filters.breached })} />SLA breached</label>
        <form className="relative ml-auto min-w-[14rem]" onSubmit={(e) => { e.preventDefault(); set({ q: text.trim() }); }}>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={text} onChange={(e) => setText(e.target.value)} onBlur={() => { if (text.trim() !== filters.q) set({ q: text.trim() }); }}
                 placeholder="Search number or title" aria-label="Search cases" className="h-8 pl-8 text-xs" />
        </form>
        {filtered && (
          <Button size="sm" variant="ghost" onClick={() => { setText(""); setFilters(DEFAULT_FILTERS); }}><X className="h-3.5 w-3.5" />Clear filters</Button>
        )}
      </div>

      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}

      {cases && !cases.length && !error ? (
        filtered
          ? <Empty icon={<LifeBuoy className="h-6 w-6" />} title="No cases match" text="Change or clear the filters to see more."
                   action={<Button size="sm" variant="outline" onClick={() => { setText(""); setFilters(DEFAULT_FILTERS); }}>Clear filters</Button>} />
          : <Empty icon={<LifeBuoy className="h-6 w-6" />} title="No open cases"
                   text="A case tracks one problem from report to verified fix. Open one from a Jira issue, an incident, a failing test, or Report a problem."
                   action={access.canCase ? <Button size="sm" onClick={() => setReporting(true)}><Plus className="h-3.5 w-3.5" />New case</Button> : undefined} />
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2">Case</th><th className="px-2 py-2">Title</th><th className="px-2 py-2">Kind</th><th className="px-2 py-2">Source</th>
                <th className="px-2 py-2">Sev</th><th className="px-2 py-2">Status</th><th className="px-2 py-2">Domain</th><th className="px-2 py-2">Assignee</th>
                <th className="px-2 py-2">SLA due</th><th className="px-2 py-2">Updated</th>
              </tr>
            </thead>
            <tbody>
              {(cases ?? []).map((c) => (
                <tr key={c.case_id} className="border-t hover:bg-muted/30">
                  <td className="whitespace-nowrap px-3 py-2"><Link href={caseHref(c.case_id)} className="font-mono text-xs font-medium text-primary hover:underline">{c.number}</Link></td>
                  <td className="max-w-[26rem] px-2 py-2">
                    <Link href={caseHref(c.case_id)} className="line-clamp-2 hover:text-primary">{c.title}</Link>
                    {c.target_fqn && <span className="block truncate font-mono text-[10px] text-muted-foreground">{c.target_fqn}</span>}
                  </td>
                  <td className="px-2 py-2"><CaseKindPill kind={c.kind} /></td>
                  <td className="px-2 py-2"><SourceChip source={c.source} /></td>
                  <td className="px-2 py-2"><SeverityPill severity={c.severity} /></td>
                  <td className="px-2 py-2"><CaseStatusPill status={c.status} /></td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{c.domain_name ?? "General"}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{c.assignee ?? "unassigned"}</td>
                  <td className={cn("whitespace-nowrap px-2 py-2 text-xs", c.sla_breached ? "font-medium text-destructive" : "text-muted-foreground")}>
                    {c.sla_due_at ? <><When iso={c.sla_due_at} />{c.sla_breached && <span className="ml-1">(breached)</span>}</> : <span className="text-muted-foreground">-</span>}
                  </td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground"><When iso={c.updated_at ?? c.opened_at} rel empty="-" /></td>
                </tr>
              ))}
              {!cases && <tr><td colSpan={10} className="px-3 py-8 text-center text-xs text-muted-foreground"><Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" />Loading cases…</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {cases && cases.length > 0 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{cases.length} of {plural(total, "case")}</span>
          {cases.length < total && (
            <Button size="sm" variant="outline" disabled={paging || loading} onClick={more}>{paging && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Load more</Button>
          )}
        </div>
      )}
      {reporting && <ReportDialog title="New case" domains={domains} onClose={() => setReporting(false)} onOpened={() => setReload((n) => n + 1)} />}
    </div>
  );
}

function StatusPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const label = sameSet(value, OPEN_STATUSES) ? "Open statuses" : !value.length ? "Any status"
    : value.length === 1 ? caseStatusLabel(value[0]) : `${value.length} statuses`;
  const toggle = (s: string) => onChange(value.includes(s) ? value.filter((x) => x !== s) : [...value, s]);
  return (
    <details className="relative">
      <summary className="flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-lg border border-input/80 bg-card px-3 text-xs">
        {label}<ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /></summary>
      <div className="absolute z-20 mt-1 w-52 space-y-0.5 rounded-xl border bg-card p-2 text-xs shadow-lg" role="group" aria-label="Statuses">
        <div className="flex gap-2 border-b pb-1.5">
          <button type="button" className="text-primary hover:underline" onClick={() => onChange(OPEN_STATUSES)}>Open</button>
          <button type="button" className="text-primary hover:underline" onClick={() => onChange([])}>Any</button>
        </div>
        {STATUSES.map((s) => (
          <label key={s} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-muted">
            <input type="checkbox" checked={value.includes(s)} onChange={() => toggle(s)} /><CaseStatusPill status={s} />
          </label>
        ))}
      </div>
    </details>
  );
}
