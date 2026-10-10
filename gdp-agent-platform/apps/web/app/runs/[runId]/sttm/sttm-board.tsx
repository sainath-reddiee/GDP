"use client";

import { useMemo, useState, useTransition } from "react";
import { ArrowRight, Check, Download, Loader2, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { applyTransformation, exportSttmCsv, refineTransformation, type TransformProposal } from "../pipeline-actions";

export type SttmLine = {
  sttm_line_id: string;
  source_table: string | null;
  source_column: string | null;
  source_datatype?: string | null;
  target_column: string;
  target_datatype: string;
  mapping_type: string;
  transformation: string | null;
  business_definition: string | null;
  human_approved: boolean;
};

export type ProfileCol = {
  table_name: string;
  column_name: string;
  data_type: string;
  semantic_type: string | null;
  null_percentage: number | null;
  distinct_percentage: number | null;
  generated_description: string | null;
};

const MAPPING_TONE: Record<string, string> = {
  DIRECT: "bg-success/10 text-success border-success/20",
  TRANSFORM: "bg-primary/10 text-primary border-primary/20",
  TRANSFORMED: "bg-primary/10 text-primary border-primary/20",
  DERIVED: "bg-violet-500/10 text-violet-600 border-violet-500/20",
  UNMAPPED: "bg-muted text-muted-foreground",
};

function MappingPill({ type }: { type: string }) {
  return <span className={cn("rounded-full border px-1.5 py-0 text-[10px] font-medium uppercase", MAPPING_TONE[type] ?? "bg-muted text-muted-foreground")}>{type.toLowerCase()}</span>;
}

/** Is the transformation more than the plain source column? */
function isPassThrough(l: SttmLine) {
  const t = (l.transformation || "").replace(/["\s]/g, "").toUpperCase();
  return !t || t.endsWith(`.${(l.source_column || "").toUpperCase()}`) || t === (l.source_column || "").toUpperCase();
}

export function SttmBoard({
  runId, lines, profiles, canEdit,
}: {
  runId: string;
  lines: SttmLine[];
  profiles: ProfileCol[];
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(lines[0]?.sttm_line_id ?? "");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "logic" | "pending">("all");
  const [prompt, setPrompt] = useState("");
  const [proposed, setProposal] = useState<{ lineId: string; data: TransformProposal } | null>(null);
  const [stagePath, setStagePath] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const line = useMemo(() => lines.find((l) => l.sttm_line_id === open) ?? lines[0], [lines, open]);
  const profile = useMemo(() => {
    if (!line?.source_column) return undefined;
    return profiles.find(
      (p) => p.column_name.toUpperCase() === line.source_column!.toUpperCase()
        && (!line.source_table || p.table_name.toUpperCase() === line.source_table.toUpperCase()),
    );
  }, [line, profiles]);
  // A proposal only belongs to the line it was asked for; switching lines hides it.
  const proposal = proposed && proposed.lineId === line?.sttm_line_id ? proposed.data : null;

  const q = query.trim().toLowerCase();
  const visible = lines.filter((l) => {
    if (q && !`${l.target_column} ${l.source_table ?? ""}.${l.source_column ?? ""}`.toLowerCase().includes(q)) return false;
    if (filter === "logic") return !isPassThrough(l);
    if (filter === "pending") return !l.human_approved;
    return true;
  });
  const counts = {
    all: lines.length,
    logic: lines.filter((l) => !isPassThrough(l)).length,
    pending: lines.filter((l) => !l.human_approved).length,
  };

  const choose = (id: string) => { setOpen(id); setProposal(null); setPrompt(""); setError(""); };

  const propose = () => {
    if (!line) return;
    const lineId = line.sttm_line_id;
    start(async () => {
      setError("");
      const result = await refineTransformation(runId, {
        sttm_line_id: line.sttm_line_id, prompt, target_column: line.target_column, source_column: line.source_column,
        source_table: line.source_table, source_datatype: line.source_datatype, target_datatype: line.target_datatype,
        current_transformation: line.transformation, business_definition: line.business_definition,
      });
      if (!result.ok) { setError(result.error); return; }
      setProposal({ lineId, data: result.data });
    });
  };

  const apply = () => {
    if (!line || !proposal) return;
    start(async () => {
      setError("");
      const result = await applyTransformation(runId, {
        sttm_line_id: line.sttm_line_id, transformation: proposal.transformation, prompt,
        rationale: proposal.rationale, dbt_notes: proposal.dbt_notes, soda_checks: proposal.soda_checks,
      });
      if (!result.ok) setError(result.error);
      else { setProposal(null); setPrompt(""); }
    });
  };

  const exportCsv = () => start(async () => {
    setError("");
    const result = await exportSttmCsv(runId);
    if (!result.ok) { setError(result.error); return; }
    setStagePath(result.data.stage_path);
    const href = URL.createObjectURL(new Blob([result.data.csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = href;
    a.download = `sttm_v${result.data.sttm_version}.csv`;
    a.click();
    { const done = href; setTimeout(() => URL.revokeObjectURL(done), 1000); }
  });

  if (!line) return null;

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold">Lines</h3>
          <p className="text-sm text-muted-foreground">One row per target column. Pick a line to see its lineage and logic, or ask Cortex to change it.</p>
        </div>
        <Button size="sm" variant="outline" onClick={exportCsv} disabled={pending}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}Export CSV to stage
        </Button>
      </div>
      {stagePath && <p className="text-xs text-muted-foreground">Stored at <span className="font-mono">{stagePath}</span></p>}

      <div className="grid overflow-hidden rounded-xl border lg:h-[min(76vh,780px)] lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="flex min-h-0 flex-col border-b bg-muted/20 lg:border-b-0 lg:border-r">
          <div className="space-y-2 border-b p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input className="pl-8" placeholder="Find a column or source" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find a line" />
            </div>
            <div className="flex flex-wrap gap-1">
              {([["all", "All"], ["logic", "With logic"], ["pending", "Not approved"]] as const).map(([k, label]) => (
                <button key={k} type="button" onClick={() => setFilter(k)} aria-pressed={filter === k}
                        className={cn("rounded-full border px-2.5 py-0.5 text-xs", filter === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40")}>
                  {label} <span className="tabular-nums opacity-70">{counts[k]}</span>
                </button>
              ))}
            </div>
          </div>
          <div role="listbox" aria-label="STTM lines" className="max-h-[50vh] flex-1 overflow-y-auto p-1.5 lg:max-h-none">
            {visible.length === 0 && <p className="p-3 text-xs text-muted-foreground">No lines match.</p>}
            {visible.map((l) => {
              const active = l.sttm_line_id === line.sttm_line_id;
              return (
                <button key={l.sttm_line_id} type="button" role="option" aria-selected={active} onClick={() => choose(l.sttm_line_id)}
                        className={cn("flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition",
                                      active ? "bg-card shadow-sm ring-1 ring-primary/40" : "hover:bg-card/70")}>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate font-mono text-[13px] font-medium">{l.target_column}</span>
                      {l.human_approved && <Check className="h-3.5 w-3.5 shrink-0 text-success" aria-label="approved" />}
                    </span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {l.source_table ? `${l.source_table}.${l.source_column}` : "derived"}
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <MappingPill type={l.mapping_type} />
                    <span className="font-mono text-[10px] text-muted-foreground">{l.target_datatype}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="min-h-0 min-w-0 space-y-4 overflow-y-auto p-5">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="font-mono text-xl font-semibold">{line.target_column}</h4>
              <MappingPill type={line.mapping_type} />
              {line.human_approved ? <Badge variant="success">approved</Badge> : <Badge variant="warning">not approved</Badge>}
            </div>
            <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs">
              <span className="font-mono">{line.source_table ? `${line.source_table}.${line.source_column}` : "derived"}</span>
              {line.source_datatype && <span className="text-muted-foreground">{line.source_datatype}</span>}
              <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-mono font-medium">{line.target_column}</span>
              <span className="text-muted-foreground">{line.target_datatype}</span>
            </div>
            {line.business_definition && <p className="text-sm text-muted-foreground">{line.business_definition}</p>}
          </div>

          {profile && (
            <div className="grid gap-2 sm:grid-cols-4">
              {[["Nulls", profile.null_percentage != null ? `${profile.null_percentage}%` : "–"],
                ["Distinct", profile.distinct_percentage != null ? `${profile.distinct_percentage}%` : "–"],
                ["Semantic", profile.semantic_type || "–"], ["Source type", profile.data_type]].map(([k, v]) => (
                <div key={k} className="rounded-lg border px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{k}</p>
                  <p className="truncate text-sm font-semibold" title={String(v)}>{v}</p>
                </div>
              ))}
              {profile.generated_description && <p className="text-xs text-muted-foreground sm:col-span-4">{profile.generated_description}</p>}
            </div>
          )}

          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Logic</p>
            <pre className="whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-relaxed">{line.transformation || "(direct)"}</pre>
          </div>

          {canEdit ? (
            <div className="space-y-2 rounded-xl border border-violet-200 bg-violet-50/40 p-3 dark:border-violet-900 dark:bg-violet-950/20">
              <p className="flex items-center gap-1.5 text-sm font-medium"><Sparkles className="h-4 w-4 text-violet-600" />Ask for a change</p>
              <Textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Ask for a change"
                        placeholder="e.g. Cast END_DATE as DATE using YYYY-MM-DD. Map Y/N to ACTIVE/INACTIVE." />
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={propose} disabled={pending || !prompt.trim()}>
                  {pending && !proposal ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Propose SQL
                </Button>
                {proposal && <Button size="sm" variant="outline" onClick={apply} disabled={pending}>Apply to contract</Button>}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Reopen STTM review to change logic. Export still works.</p>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

          {proposal && (
            <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Proposed logic</p>
              <pre className="whitespace-pre-wrap break-words font-mono text-xs">{proposal.transformation}</pre>
              {proposal.rationale && <p className="text-sm text-muted-foreground">{proposal.rationale}</p>}
              {proposal.dbt_notes && <p className="text-xs text-muted-foreground">dbt: {proposal.dbt_notes}</p>}
              {(proposal.soda_checks?.length ?? 0) > 0 && (
                <ul className="list-disc pl-5 text-sm">
                  {proposal.soda_checks!.map((c, i) => <li key={i}>{c.check_type}: {c.requirement}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
