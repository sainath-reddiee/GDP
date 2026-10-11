"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { ArrowUpRight, FlaskConical, Loader2, PackagePlus, Search, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PackRow, RunTest, RunTestSummary } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { Drawer, Modal } from "@/components/hub/overlay";
import { EmptyState, StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { openTestCase, promoteTests } from "./actions";

const OUTCOME_FILL: Record<string, string> = { PASS: "#10b981", FAIL: "#f43f5e", ERROR: "#f43f5e", REVIEW: "#f59e0b", NOT_RUN: "#94a3b8" };

function History({ points }: { points: { outcome: string; ran_at: string }[] }) {
  if (!points.length) return <span className="text-[11px] text-muted-foreground">never run</span>;
  return (
    <svg width={points.length * 6} height={14} role="img" aria-label={`Last ${points.length} runs`}>
      {points.map((p, i) => (
        <rect key={i} x={i * 6} y={p.outcome === "PASS" ? 4 : 0} width={4} height={p.outcome === "PASS" ? 10 : 14} rx={1} fill={OUTCOME_FILL[p.outcome] ?? "#94a3b8"}>
          <title>{`${p.ran_at.slice(0, 16)} · ${p.outcome.toLowerCase()}`}</title>
        </rect>
      ))}
    </svg>
  );
}

function TestDrawer({ test, onClose, canCase }: { test: RunTest; onClose: () => void; canCase: boolean }) {
  const toast = useToast();
  const [pending, start] = useTransition();
  const rows = test.sample_rows ?? [];
  const cols = Object.keys(rows[0] ?? {});
  const caseFor = () => start(async () => {
    const r = await openTestCase(test.result_id!);
    if (r.ok) toast("ok", `Case ${r.data.case.number} opened`);
    else if (r.pending) toast("info", r.error);
    else toast("error", explain(r.error).message);
  });
  return (
    <Drawer open onClose={onClose} wide title={test.title} subtitle={[test.category?.toLowerCase(), test.severity?.toLowerCase(), test.scope === "TABLE" ? test.target_fqn : test.run_name].filter(Boolean).join(" · ")}>
      <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">{test.outcome ? <StatusPill status={test.outcome} /> : <StatusPill status="NEVER_RUN" />}<History points={test.history} />
          {test.run_id && <Link href={`/runs/${test.run_id}/qa`} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">Open in the run <ArrowUpRight className="h-3 w-3" /></Link>}</div>
        {test.objective && <p className="text-muted-foreground">{test.objective}</p>}
        <div><p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">SQL · expected {test.expected ?? "0 rows"}</p>
          <pre className="max-h-64 overflow-auto rounded-lg bg-muted p-2 font-mono text-[11px]">{test.sql_text}</pre></div>
        {test.outcome && (
          <div className="space-y-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Latest result · {test.ran_at?.slice(0, 16)}</p>
            <p>{test.detail ?? "-"}{test.rows_returned != null ? ` · ${test.rows_returned} rows` : ""}</p>
            {rows.length > 0 && (
              <div className="max-h-56 overflow-auto rounded-lg border">
                <table className="w-full text-[11px]">
                  <thead className="bg-muted/60"><tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-mono font-medium">{c}</th>)}</tr></thead>
                  <tbody>{rows.map((r, i) => <tr key={i} className="border-t">{cols.map((c) => <td key={c} className="px-2 py-1 font-mono">{r[c] == null ? "null" : String(r[c])}</td>)}</tr>)}</tbody>
                </table>
              </div>
            )}
            {canCase && test.result_id && test.outcome !== "PASS" && <Button size="sm" variant="outline" onClick={caseFor} disabled={pending}><ShieldAlert className="h-3.5 w-3.5" /> Open a case</Button>}
          </div>
        )}
      </div>
    </Drawer>
  );
}

function PromoteModal({ ids, packs, domains, scope, onClose, onDone }: {
  ids: string[]; packs: PackRow[]; domains: { domain_id: string; domain_name: string }[]; scope: string; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [domain, setDomain] = useState(domains.find((d) => d.domain_name === scope)?.domain_id ?? domains[0]?.domain_id ?? "");
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");
  const [pending, start] = useTransition();
  const inDomain = packs.filter((p) => p.domain_id === domain && p.status !== "RETIRED");
  const go = () => start(async () => {
    const r = await promoteTests({ test_ids: ids, domain, pack_id: target || null, name: target ? null : name.trim() });
    if (r.ok) { toast("ok", `${r.data.added} test${r.data.added === 1 ? "" : "s"} added to a pack draft${r.data.skipped ? `, ${r.data.skipped} already there` : ""}. Submit the pack for approval when ready.`); onDone(); }
    else if (r.pending) { toast("info", r.error); onDone(); }
    else toast("error", explain(r.error).message);
  });
  return (
    <Modal open onClose={onClose} title={`Add ${ids.length} test${ids.length === 1 ? "" : "s"} to a QA pack`}
           description="The tests become queries in a pack draft. Nothing runs in pipelines until the pack version is submitted and approved."
           footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
             <Button onClick={go} disabled={pending || !domain || (!target && name.trim().length < 1)} data-testid="confirm-promote">{pending && <Loader2 className="h-4 w-4 animate-spin" />} Add to pack</Button></>}>
      <Label htmlFor="pr-domain">Domain</Label>
      <Select id="pr-domain" value={domain} onChange={(e) => { setDomain(e.target.value); setTarget(""); }}>
        {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
      </Select>
      <Label htmlFor="pr-pack">Pack</Label>
      <Select id="pr-pack" value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="">A new pack</option>
        {inDomain.map((p) => <option key={p.pack_id} value={p.pack_id}>{p.name} (v{p.version}, {p.status.toLowerCase().replace(/_/g, " ")})</option>)}
      </Select>
      {!target && <><Label htmlFor="pr-name">New pack name</Label><Input id="pr-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Customer integrity" /></>}
    </Modal>
  );
}

export function RunTestsView({ tests, summary, packs, domains, scope, canPromote, canCase }: {
  tests: RunTest[]; summary: RunTestSummary; packs: PackRow[]; domains: { domain_id: string; domain_name: string }[]; scope: string;
  canPromote: boolean; canCase: boolean;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [outcome, setOutcome] = useState<"all" | "failing" | "passing" | "never">("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<RunTest | null>(null);
  const [promote, setPromote] = useState(false);
  const shown = useMemo(() => tests.filter((t) => (!q || `${t.title} ${t.target_fqn ?? ""} ${t.run_name ?? ""} ${t.category ?? ""}`.toLowerCase().includes(q.toLowerCase()))
    && (outcome === "all" || (outcome === "failing" ? ["FAIL", "ERROR"].includes(t.outcome ?? "") : outcome === "passing" ? t.outcome === "PASS" : !t.outcome))), [tests, q, outcome]);
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  if (!tests.length) return <EmptyState icon={FlaskConical} title="No run tests yet">Tests saved on a run&apos;s QA page or in the QA workspace appear here.</EmptyState>;
  return (
    <div className="space-y-3" data-testid="run-tests">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by test, table or run" className="pl-9" aria-label="Filter tests" />
        </div>
        <div className="flex rounded-xl bg-muted p-1 text-xs">
          {(["all", "failing", "passing", "never"] as const).map((o) => (
            <button key={o} type="button" onClick={() => setOutcome(o)} className={cn("rounded-lg px-2.5 py-1 font-medium capitalize transition", outcome === o ? "bg-card shadow-xs" : "text-muted-foreground")}>{o === "never" ? "never run" : o}</button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">{summary.executed} of {summary.tests} executed{summary.pass_rate != null ? ` · ${summary.pass_rate}% passing` : ""}</span>
      </div>
      {canPromote && picked.size > 0 && (
        <div className="flex items-center gap-2 text-sm"><span className="font-medium">{picked.size} selected</span>
          <Button size="sm" onClick={() => setPromote(true)} data-testid="promote-tests"><PackagePlus className="h-3.5 w-3.5" /> Add to a QA pack</Button></div>
      )}
      <section className="surface overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr>{canPromote && <th className="w-8 px-3" />}<th className="px-3 py-2 text-left">Test</th><th className="px-3 py-2 text-left">Where</th>
              <th className="px-3 py-2 text-left">Latest</th><th className="px-3 py-2 text-left">History</th></tr>
          </thead>
          <tbody>
            {shown.slice(0, 400).map((t) => (
              <tr key={t.test_id} className="border-t border-border/60 align-top hover:bg-muted/30">
                {canPromote && <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${t.title}`} checked={picked.has(t.test_id)} onChange={() => toggle(t.test_id)} /></td>}
                <td className="px-3 py-2"><button type="button" onClick={() => setOpen(t)} className="text-left font-medium text-primary hover:underline" data-testid={`test-${t.test_id}`}>{t.title}</button>
                  <p className="text-[11px] text-muted-foreground">{[t.category?.toLowerCase(), t.severity?.toLowerCase(), t.origin?.toLowerCase()].filter(Boolean).join(" · ")}</p></td>
                <td className="px-3 py-2 text-xs">{t.scope === "TABLE" ? <span className="font-mono">{t.target_fqn}</span> : t.run_name ?? "-"}<p className="text-[11px] text-muted-foreground">{t.domain_name ?? ""}</p></td>
                <td className="px-3 py-2">{t.outcome ? <StatusPill status={t.outcome} /> : <span className="text-xs text-muted-foreground">-</span>}</td>
                <td className="px-3 py-2"><History points={t.history} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length > 400 && <p className="border-t p-2 text-center text-xs text-muted-foreground">Showing 400 of {shown.length}; narrow the filter.</p>}
        {!shown.length && <p className="p-4 text-center text-sm text-muted-foreground">No tests match.</p>}
      </section>
      {open && <TestDrawer test={open} onClose={() => setOpen(null)} canCase={canCase} />}
      {promote && <PromoteModal ids={[...picked]} packs={packs} domains={domains} scope={scope} onClose={() => setPromote(false)}
                                onDone={() => { setPromote(false); setPicked(new Set()); router.refresh(); }} />}
    </div>
  );
}
