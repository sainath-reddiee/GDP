"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { GitCompareArrows, Loader2, RotateCcw, Save, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmDiff, DmIssue, DmVersion } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { loadDiff, saveStandard } from "../model-actions";
import { DiffList } from "./review-panel";

export function VersionsPanel({ modelId, versions, approved, canRollback, onRollback }: {
  modelId: string; versions: DmVersion[]; approved: number | null; canRollback: boolean; onRollback: (version: number) => void;
}) {
  const toast = useToast();
  const [from, setFrom] = useState(versions[1]?.version ?? versions[0]?.version ?? 1);
  const [to, setTo] = useState(versions[0]?.version ?? 1);
  const [diff, setDiff] = useState<DmDiff | null>(null);
  const [pending, start] = useTransition();
  const compare = () => start(async () => {
    const r = await loadDiff(modelId, to, from);
    if (r.ok) setDiff(r.data); else toast("error", explain(r.error).message);
  });
  return (
    <div className="space-y-4">
      <section className="surface overflow-x-auto" data-testid="versions">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr><th className="px-3 py-2 text-left">Version</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Change</th>
              <th className="px-3 py-2 text-left">People</th><th className="px-3 py-2 text-left">When</th><th /></tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version} className="border-t border-border/60 align-top">
                <td className="px-3 py-2"><Link href={`/os/design/modeling/${modelId}?version=${v.version}`} className="font-medium text-primary hover:underline">v{v.version}</Link>
                  {v.version === approved && <span className="ml-1 text-[10px] font-semibold uppercase text-success">current</span>}</td>
                <td className="px-3 py-2"><StatusPill status={v.status} /><p className="text-[11px] text-muted-foreground">{(v.origin ?? "").toLowerCase()}</p></td>
                <td className="px-3 py-2 text-xs">{v.change_note || "-"}
                  {v.review_summary && <p className="text-[11px] text-muted-foreground">{v.review_summary.counts.BREAKING} breaking · {v.review_summary.counts.ADDITIVE} additive · {v.review_summary.counts.NON_FUNCTIONAL} documentation</p>}
                  {v.reject_reason && <p className="text-[11px] text-destructive">Rejected: {v.reject_reason}</p>}</td>
                <td className="px-3 py-2 text-xs">{v.authors.join(", ") || v.created_by}
                  {v.submitted_by && <p className="text-[11px] text-muted-foreground">submitted by {v.submitted_by}</p>}
                  {v.approved_by && <p className="text-[11px] text-muted-foreground">approved by {v.approved_by}</p>}</td>
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{(v.approved_at ?? v.submitted_at ?? v.updated_at ?? v.created_at ?? "").slice(0, 16)}</td>
                <td className="px-3 py-2">{canRollback && v.version !== approved && ["APPROVED", "PUBLISHED", "SUPERSEDED"].includes(v.status) && (
                  <Button size="sm" variant="ghost" onClick={() => onRollback(v.version)} data-testid={`rollback-${v.version}`}><RotateCcw className="h-3.5 w-3.5" /> Roll back to this</Button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {versions.length > 1 && (
        <section className="surface space-y-3 p-4">
          <div className="flex flex-wrap items-end gap-2">
            <div><Label htmlFor="cmp-from">From</Label><Select id="cmp-from" className="h-9 w-28" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
              {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
            <div><Label htmlFor="cmp-to">To</Label><Select id="cmp-to" className="h-9 w-28" value={to} onChange={(e) => setTo(Number(e.target.value))}>
              {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
            <Button variant="outline" onClick={compare} disabled={pending || from === to} data-testid="compare-versions">
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompareArrows className="h-4 w-4" />} Compare</Button>
          </div>
          {diff && <DiffList diff={diff} />}
          <p className="text-xs text-muted-foreground">A rollback opens a new draft with the older version&apos;s content; it is reviewed and approved like any change.</p>
        </section>
      )}
    </div>
  );
}

const PRESETS = ["NONE", "GDP", "COMPANY", "CUSTOM"];

export function StandardsPanel({ modelId, issues, standard, canEdit, onPick }: {
  modelId: string; issues: DmIssue[]; standard: Record<string, unknown>; canEdit: boolean; onPick: (path: string) => void;
}) {
  const router = useRouter();
  const toast = useToast();
  const [preset, setPreset] = useState(String(standard.preset ?? "NONE"));
  const [prefix, setPrefix] = useState(String(standard.table_prefix ?? ""));
  const [audit, setAudit] = useState(((standard.audit_columns as string[]) ?? []).join(", "));
  const [pending, start] = useTransition();
  const errors = issues.filter((i) => i.severity === "ERROR"), warns = issues.filter((i) => i.severity === "WARN");
  const save = () => start(async () => {
    const r = await saveStandard(modelId, { preset, table_prefix: prefix, audit_columns: audit.split(",").map((a) => a.trim().toUpperCase()).filter(Boolean) });
    if (r.ok) { toast("ok", "Naming standard saved; drafts are checked against it"); router.refresh(); } else toast("error", explain(r.error).message);
  });
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <section className="surface space-y-3 p-4" data-testid="issues">
        <h3 className="text-sm font-semibold">Checks on this version</h3>
        {!issues.length && <p className="text-sm text-success">No problems: names, types, keys, relationships and the naming standard all pass.</p>}
        {[["Errors (block submit and approval)", errors], ["Warnings", warns]].map(([label, list]) => (list as DmIssue[]).length > 0 && (
          <div key={label as string}>
            <p className="mb-1 text-xs font-semibold text-muted-foreground">{label as string}</p>
            <ul className="space-y-1">
              {(list as DmIssue[]).map((i, n) => (
                <li key={n}>
                  <button type="button" onClick={() => onPick(i.path)} className={cn("flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left text-xs hover:bg-muted", i.severity === "ERROR" ? "text-destructive" : "text-amber-800 dark:text-amber-300")}>
                    <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" /><span className="font-mono">{i.path}</span><span className="text-foreground">{i.message}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
      <section className="surface space-y-2 p-4">
        <h3 className="text-sm font-semibold">Naming standard</h3>
        <p className="text-xs text-muted-foreground">Drafts are checked against it (warnings). It never changes approved versions.</p>
        <Label htmlFor="std-preset">Preset</Label>
        <Select id="std-preset" value={preset} disabled={!canEdit} onChange={(e) => setPreset(e.target.value)}>{PRESETS.map((p) => <option key={p}>{p}</option>)}</Select>
        {preset === "CUSTOM" && (
          <>
            <Label htmlFor="std-prefix">Table prefix</Label><Input id="std-prefix" className="font-mono" value={prefix} disabled={!canEdit} onChange={(e) => setPrefix(e.target.value.toUpperCase())} />
            <Label htmlFor="std-audit">Audit columns</Label><Input id="std-audit" className="font-mono" value={audit} disabled={!canEdit} onChange={(e) => setAudit(e.target.value)} placeholder="LOAD_TS, RECORD_SOURCE" />
          </>
        )}
        {canEdit && <Button size="sm" onClick={save} disabled={pending}><Save className="h-3.5 w-3.5" /> Save standard</Button>}
      </section>
    </div>
  );
}
