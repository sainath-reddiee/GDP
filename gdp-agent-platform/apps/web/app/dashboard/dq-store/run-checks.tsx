"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { ArrowUpRight, Check, ClipboardCheck, Loader2, Radar, Search, ShieldAlert, Upload, X } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import type { RunCheck, RunCheckCoverage, RunCheckRun } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Drawer, Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { decideRunChecks, openCase, runCoverage, seedChecks } from "./actions";

const STATUSES = ["ALL", "PROPOSED", "APPROVED", "REJECTED"] as const;

function SampleRows({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Object.keys(rows[0] ?? {});
  return (
    <div className="max-h-56 overflow-auto rounded-lg border">
      <table className="w-full text-[11px]">
        <thead className="bg-muted/60"><tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-mono font-medium">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} className="border-t">{cols.map((c) => <td key={c} className="px-2 py-1 font-mono">{r[c] == null ? <span className="text-muted-foreground">null</span> : String(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function CheckDrawer({ check, onClose, canCase }: { check: RunCheck; onClose: () => void; canCase: boolean }) {
  const toast = useToast();
  const [pending, start] = useTransition();
  const evidence = typeof check.evidence === "string" ? check.evidence : check.evidence ? JSON.stringify(check.evidence) : null;
  const caseFor = () => start(async () => {
    const r = await openCase({ check_result_id: check.result_id! });
    if (r.ok) toast("ok", `Case ${r.data.case.number} opened`);
    else if (r.pending) toast("info", r.error);
    else toast("error", explain(r.error).message);
  });
  return (
    <Drawer open onClose={onClose} wide title={<span className="font-mono">{check.target_table}{check.target_column ? `.${check.target_column}` : ""}</span>}
            subtitle={`${check.check_type.toLowerCase().replace(/_/g, " ")} · ${check.severity.toLowerCase()} · from ${check.run_name ?? "a run"}`}>
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2"><StatusPill status={check.status} />{check.outcome && <StatusPill status={check.outcome} />}
          <span className="text-xs text-muted-foreground">{check.origin?.toLowerCase()}{check.reviewed_by ? ` · reviewed by ${check.reviewed_by}` : ""}</span>
          <Link href={`/os/build/runs/${check.run_id}/soda`} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">Open in the run <ArrowUpRight className="h-3 w-3" /></Link></div>
        {check.sodacl && <div><p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">SodaCL</p><pre className="overflow-auto rounded-lg bg-muted p-2 font-mono text-[11px]">{check.sodacl}</pre></div>}
        {check.client_requirement && <div><p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Client requirement</p><p>{check.client_requirement}</p></div>}
        {evidence && <div><p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Evidence</p><p className="text-muted-foreground">{evidence}</p></div>}
        {check.backtest?.status && <p className="text-xs">Backtest: <StatusPill status={check.backtest.status} /> {check.backtest.detail}</p>}
        {check.outcome && (
          <div className="space-y-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Latest result · {check.checked_at?.slice(0, 16)}</p>
            <p>{check.detail ?? "-"}{check.failed_rows != null ? ` · ${check.failed_rows} failing rows` : ""}{check.measured != null ? ` · measured ${check.measured}` : ""}</p>
            {check.sample_rows && check.sample_rows.length > 0 && <SampleRows rows={check.sample_rows} />}
            {check.sql_text && <pre className="max-h-48 overflow-auto rounded-lg bg-muted p-2 font-mono text-[11px]">{check.sql_text}</pre>}
            {canCase && check.result_id && (check.outcome === "FAIL" || check.outcome === "ERROR") && (
              <Button size="sm" variant="outline" onClick={caseFor} disabled={pending}><ShieldAlert className="h-3.5 w-3.5" /> Open a case</Button>
            )}
          </div>
        )}
      </div>
    </Drawer>
  );
}

function DecideModal({ runId, ids, decision, onClose, onDone }: { runId: string; ids: string[]; decision: "APPROVED" | "REJECTED"; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [pending, start] = useTransition();
  const go = () => start(async () => {
    const r = await decideRunChecks(runId, ids.map((id) => ({ expectation_id: id, decision, justification: note.trim() })));
    if (r.ok) { toast("ok", `${r.data.decided} check${r.data.decided === 1 ? "" : "s"} ${decision.toLowerCase()}`); onDone(); }
    else if (r.pending) { toast("info", r.error); onDone(); }
    else toast("error", explain(r.error).message);
  });
  return (
    <Modal open onClose={onClose} title={`${decision === "APPROVED" ? "Approve" : "Reject"} ${ids.length} check${ids.length === 1 ? "" : "s"}`}
           description="Store decisions go to DQ_APPROVER and are applied to the run when approved."
           footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={go} disabled={pending || note.trim().length < 3} data-testid="confirm-decision">{pending && <Loader2 className="h-4 w-4 animate-spin" />} Submit for approval</Button></>}>
      <Label htmlFor="decide-note">Justification</Label>
      <Textarea id="decide-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why these checks should (not) guard the model" />
    </Modal>
  );
}

export function RunChecksView({ checks, runs, initialRun, canEdit, canCase }: { checks: RunCheck[]; runs: RunCheckRun[]; initialRun: string; canEdit: boolean; canCase: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const [run, setRun] = useState(initialRun);
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("ALL");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<RunCheck | null>(null);
  const [decide, setDecide] = useState<"APPROVED" | "REJECTED" | null>(null);
  const [coverage, setCoverage] = useState<RunCheckCoverage | null>(null);
  const [pending, start] = useTransition();
  const shown = useMemo(() => checks.filter((c) => (!run || c.run_id === run) && (status === "ALL" || c.status === status)
    && (!q || `${c.target_table} ${c.target_column ?? ""} ${c.check_type} ${c.sodacl ?? ""}`.toLowerCase().includes(q.toLowerCase()))), [checks, run, status, q]);
  const selectedRun = runs.find((r) => r.run_id === run);
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const pickRun = (id: string) => { setRun(id); setPicked(new Set()); setCoverage(null); };
  const loadCoverage = () => start(async () => {
    const r = await runCoverage(run);
    if (r.ok) setCoverage(r.data); else toast("error", explain(r.error).message);
  });
  const promote = () => start(async () => {
    const r = await seedChecks({ run_id: run });
    if (r.ok) toast("ok", `${r.data.added ?? 0} check${r.data.added === 1 ? "" : "s"} added to draft v${r.data.version} of ${r.data.table}${r.data.skipped ? `, ${r.data.skipped} already there` : ""}; submit it for review in the store catalog`);
    else if (r.pending) toast("info", r.error);
    else toast("error", explain(r.error).message);
  });

  if (!checks.length) return <EmptyState icon={ClipboardCheck} title="No run checks yet">A run&apos;s data quality step proposes checks here.</EmptyState>;
  return (
    <div className="space-y-3" data-testid="run-checks">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Run" className="h-9 w-72" value={run} onChange={(e) => pickRun(e.target.value)}>
          <option value="">All runs ({runs.length})</option>
          {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_name ?? r.run_id.slice(0, 8)} · {r.checks} checks{r.proposed ? ` · ${r.proposed} to decide` : ""}</option>)}
        </Select>
        <div className="flex rounded-xl bg-muted p-1 text-xs">
          {STATUSES.map((s) => (
            <button key={s} type="button" onClick={() => setStatus(s)} className={cn("rounded-lg px-2.5 py-1 font-medium capitalize transition", status === s ? "bg-card shadow-xs" : "text-muted-foreground")}>{s.toLowerCase()}</button>
          ))}
        </div>
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by table, column or check" className="pl-9" aria-label="Filter checks" />
        </div>
      </div>
      {selectedRun && (
        <div className="surface flex flex-wrap items-center gap-3 p-3 text-sm">
          <span className="font-medium">{selectedRun.run_name}</span>
          <span className="text-xs text-muted-foreground">{selectedRun.approved} approved · {selectedRun.proposed} proposed · {selectedRun.rejected} rejected{selectedRun.failing ? ` · ${selectedRun.failing} failing` : ""}</span>
          <span className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" onClick={loadCoverage} disabled={pending}><Radar className="h-3.5 w-3.5" /> Coverage</Button>
            {canEdit && selectedRun.approved > 0 && <Button size="sm" variant="outline" onClick={promote} disabled={pending}><Upload className="h-3.5 w-3.5" /> Promote approved to the store</Button>}
          </span>
          {coverage && (
            <p className="w-full text-xs" data-testid="coverage">
              {coverage.coverage == null ? "No Data Mapping columns." : <><span className="font-semibold">{coverage.coverage}%</span> of {coverage.columns} Data Mapping columns have a check.</>}
              {coverage.missing.length > 0 && <> Without one: <span className="font-mono">{coverage.missing.slice(0, 12).map((m) => m.target_column).join(", ")}{coverage.missing.length > 12 ? ` +${coverage.missing.length - 12}` : ""}</span></>}
            </p>
          )}
        </div>
      )}
      {canEdit && run && picked.size > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium">{picked.size} selected</span>
          <Button size="sm" onClick={() => setDecide("APPROVED")} data-testid="approve-checks"><Check className="h-3.5 w-3.5" /> Approve</Button>
          <Button size="sm" variant="outline" onClick={() => setDecide("REJECTED")}><X className="h-3.5 w-3.5" /> Reject</Button>
        </div>
      )}
      {canEdit && !run && <p className="text-xs text-muted-foreground">Choose a run to approve or reject its checks.</p>}
      <section className="surface overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr>{canEdit && run && <th className="w-8 px-3" />}<th className="px-3 py-2 text-left">Check</th><th className="px-3 py-2 text-left">Run</th>
              <th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Latest result</th></tr>
          </thead>
          <tbody>
            {shown.slice(0, 400).map((c) => (
              <tr key={c.expectation_id} className="border-t border-border/60 align-top hover:bg-muted/30">
                {canEdit && run && <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${c.target_column ?? c.target_table} ${c.check_type}`} checked={picked.has(c.expectation_id)} onChange={() => toggle(c.expectation_id)} /></td>}
                <td className="px-3 py-2">
                  <button type="button" onClick={() => setOpen(c)} className="text-left font-mono text-xs font-medium text-primary hover:underline" data-testid={`check-${c.expectation_id}`}>
                    {c.target_column ?? c.target_table.split(".").pop()} · {c.check_type.toLowerCase().replace(/_/g, " ")}</button>
                  <p className="max-w-[420px] truncate font-mono text-[11px] text-muted-foreground">{c.sodacl?.split("\n").pop()?.trim()}</p>
                </td>
                <td className="px-3 py-2 text-xs">{c.run_name ?? c.run_id.slice(0, 8)}<p className="text-[11px] text-muted-foreground">{c.domain_name ?? ""}</p></td>
                <td className="px-3 py-2"><StatusPill status={c.status} /></td>
                <td className="px-3 py-2 text-xs">{c.outcome ? <><StatusPill status={c.outcome} /> <span className="text-muted-foreground">{c.checked_at?.slice(0, 10)}</span></> : <span className="text-muted-foreground">not run</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length > 400 && <p className="border-t p-2 text-center text-xs text-muted-foreground">Showing 400 of {shown.length}; narrow the filter.</p>}
        {!shown.length && <p className="p-4 text-center text-sm text-muted-foreground">No checks match.</p>}
      </section>
      {open && <CheckDrawer check={open} onClose={() => setOpen(null)} canCase={canCase} />}
      {decide && <DecideModal runId={run} ids={[...picked]} decision={decide} onClose={() => setDecide(null)} onDone={() => { setDecide(null); setPicked(new Set()); router.refresh(); }} />}
    </div>
  );
}
