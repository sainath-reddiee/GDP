"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, GitBranch, KeyRound, Layers, Loader2, ShieldAlert, Wand2, X } from "lucide-react";
import type { AnalyzeResult } from "@/lib/types";
import { DEFAULT_BANDS } from "@/lib/types";
import type { DomainRow, ModelGraph } from "@/app/onboarding/intent-types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ModelEr } from "@/components/model-er";
import { cn } from "@/lib/utils";
import { analyzeTables, catalogModelingRun } from "./actions";
import { GradeChip } from "./profile-drawer";
import { useScrollLock } from "@/components/use-scroll-lock";

type Mode = "existing" | "new";
type Standard = "GDP" | "GENERIC";

/** Replaces the onboarding wizard: everything needed to start modeling a set of staged tables, on one panel. */
export function ModelPanel({ database, schema, tables, domains, onClose }: {
  database: string; schema: string; tables: string[]; domains: DomainRow[]; onClose: () => void;
}) {
  useScrollLock();
  const router = useRouter();
  const [data, setData] = useState<AnalyzeResult | null>(null);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<Mode>("new");
  const [picked, setPicked] = useState<string[]>([]);
  const [domainId, setDomainId] = useState("");
  const [runName, setRunName] = useState("");
  const [standard, setStandard] = useState<Standard | null>(null);
  const [pending, start] = useTransition();

  useEffect(() => {
    let live = true;
    analyzeTables(database, schema, tables).then((r) => {
      if (!live) return;
      if (!r.ok) { setError(r.error); return; }
      setData(r.data);
      setStandard(r.data.suggested_standard === "GDP" ? "GDP" : "GENERIC");
      const detected = r.data.domain?.detected;
      if (detected && domains.some((d) => d.domain_id === detected.domain_id)) setDomainId(detected.domain_id);
      const rec0 = r.data.models.recommendation;
      if (rec0 && (rec0.action === "MAP_EXISTING" || rec0.action === "EXTEND_EXISTING") && rec0.fqn) {
        setMode("existing"); setPicked([rec0.fqn]);
      } else {
        const strongFqns = r.data.models.suggestions.filter((s) => s.kind === "existing" && s.score >= (r.data.bands ?? DEFAULT_BANDS).model_match_strong).map((s) => s.fqn);
        if (strongFqns.length) { setMode("existing"); setPicked(strongFqns); }
      }
    });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { live = false; window.removeEventListener("keydown", onKey); };
  }, [database, schema, tables, domains, onClose]);

  const existing = (data?.models.suggestions ?? []).filter((s) => s.kind === "existing");
  const proposed = (data?.models.suggestions ?? []).find((s) => s.kind === "proposed");

  const graph: ModelGraph | null = useMemo(() => {
    if (!data) return null;
    const chosen = mode === "existing" ? existing.filter((s) => picked.includes(s.fqn)) : [];
    const targets = chosen.length
      ? chosen.map((s) => ({ target_table: s.target_table, fqn: s.fqn, domain_name: s.domain_name ?? undefined, selected: true, columns: [] }))
      : proposed ? [{ target_table: proposed.target_table, fqn: proposed.fqn, selected: true, columns: [] }] : [];
    return {
      ...data.graph,
      targets,
      edges: data.graph.sources.flatMap((s) => targets.map((t) => ({ from: s.object_name, to: t.target_table, kind: "planned" }))),
    };
  }, [data, mode, picked, existing, proposed]);

  const [proposedName, setProposedName] = useState("");
  const [proposedSchema, setProposedSchema] = useState("SILVER");

  const create = () => start(async () => {
    setError("");
    const targets = mode === "existing"
      ? existing.filter((s) => picked.includes(s.fqn)).map((s) => ({
          fqn: s.fqn, target_table: s.target_table, domain_name: s.domain_name ?? null,
          target_table_id: data?.models.targets.find((t) => t.fqn === s.fqn)?.target_table_id ?? null,
        }))
      : [];
    const r = await catalogModelingRun({
      database, schema, tables, run_name: runName || null, domain_id: domainId || null, targets,
      modeling_standard: standard ?? "GENERIC",
      ...(mode === "new" ? { proposed_name: proposedName.trim() || null, proposed_schema: proposedSchema.trim() || null } : {}),
    });
    if (!r.ok) { setError(r.error); return; }
    router.push(r.data.error ? `/runs/${r.data.run_id}/source` : `/runs/${r.data.run_id}/mapping`);
  });

  const rec = data?.models.recommendation;
  const detected = data?.domain?.detected ?? null;
  const schemaDomain = data?.domain?.schema ?? null;
  const strong = (data?.bands ?? DEFAULT_BANDS).model_match_strong;
  const recApplied = !!rec && (rec.fqn ? mode === "existing" && picked.length === 1 && picked[0] === rec.fqn : mode === "new");
  const applyRecommendation = () => {
    if (!rec) return;
    if (rec.fqn) { setMode("existing"); setPicked([rec.fqn]); }
    else { setMode("new"); setProposedName(rec.target_table); }
  };

  const avg = data?.tables.filter((t) => t.quality?.overall != null) ?? [];
  const avgScore = avg.length ? avg.reduce((n, t) => n + (t.quality!.overall as number), 0) / avg.length : null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label="Start modeling"
             className="relative flex h-full w-[1040px] max-w-full flex-col overflow-hidden border-l bg-background shadow-2xl">
        <header className="flex items-start gap-3 border-b px-6 py-4">
          <Layers className="mt-0.5 h-5 w-5 text-primary" />
          <div>
            <h3 className="text-base font-semibold">Model {tables.length} staged table{tables.length === 1 ? "" : "s"}</h3>
            <p className="font-mono text-[11px] text-muted-foreground">{database}.{schema}</p>
          </div>
          <button type="button" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto overscroll-contain px-6 py-5">
          {!data && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Reading staged profiles, inferring relationships and matching models…
            </p>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

          {data && (
            <>
              {/* 1. The decision first: what to model and where it goes */}
              {rec && (
                <section className={cn("rounded-xl border p-4", REC_TONE[rec.action])}>
                  <div className="flex flex-wrap items-start gap-3">
                    <Wand2 className="mt-0.5 h-5 w-5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-semibold uppercase tracking-wide opacity-80">Recommendation</p>
                      <p className="text-base font-semibold">{rec.headline}</p>
                      <ul className="mt-1 space-y-0.5 text-xs text-foreground/80">
                        {rec.why.map((w) => <li key={w}>• {w}</li>)}
                      </ul>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                      {rec.action !== "NEW" && <span className="rounded-full bg-card px-2 py-0.5 text-xs font-semibold tabular-nums" title="How well the model fits">{Math.round(rec.confidence * 100)}% fit</span>}
                      {!recApplied && <Button size="sm" onClick={applyRecommendation}><Check className="h-3.5 w-3.5" />Use this</Button>}
                      {recApplied && <span className="flex items-center gap-1 text-[11px] font-medium"><Check className="h-3.5 w-3.5" />applied</span>}
                    </div>
                  </div>
                </section>
              )}

              <section className="grid gap-4 lg:grid-cols-2">
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Standard</h4>
                  <div role="radiogroup" aria-label="Modeling standard" className="grid grid-cols-2 gap-2">
                    {([
                      ["GDP", "GDP", "Hub and spoke, GDP keys, audit columns and GDP packs"],
                      ["GENERIC", "Another source", "Plain model, no GDP conventions"],
                    ] as const).map(([value, title, body]) => (
                      <button key={value} type="button" role="radio" aria-checked={standard === value} onClick={() => setStandard(value)}
                              className={cn("rounded-lg border p-2.5 text-left", standard === value ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:bg-muted/40")}>
                        <p className="text-sm font-semibold">{title}</p>
                        <p className="text-[11px] text-muted-foreground">{body}</p>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Domain</h4>
                  {detected ? (
                    <div className="rounded-lg border border-primary/30 bg-primary/5 p-2.5 text-sm">
                      <p><span className="font-semibold">{detected.domain_name}</span>
                        <span className="ml-2 text-xs text-muted-foreground">
                          {detected.inherited_from_schema && schemaDomain
                            ? `schema ${Math.round(schemaDomain.confidence * 100)}% · these tables ${Math.round(detected.confidence * 100)}%`
                            : `${Math.round(detected.confidence * 100)}%`}
                        </span></p>
                      {detected.signals.length > 0 && <p className="mt-0.5 truncate text-[11px] text-muted-foreground" title={detected.signals.join(", ")}>Signals: {detected.signals.join(", ")}</p>}
                    </div>
                  ) : schemaDomain ? (
                    <div className="rounded-lg border border-dashed p-2.5 text-xs text-muted-foreground">
                      The schema looks like <span className="font-semibold text-foreground">{schemaDomain.domain_name}</span> ({Math.round(schemaDomain.confidence * 100)}%),
                      but these tables carry none of its signals. Pick the pack below if they belong to it.
                    </div>
                  ) : (
                    <p className="rounded-lg border border-dashed p-2.5 text-xs text-muted-foreground">No domain matched with enough confidence; pick a pack below or leave it to inference.</p>
                  )}
                  <select id="model_domain" value={domainId} onChange={(e) => setDomainId(e.target.value)} aria-label="Domain knowledge pack"
                          className="h-9 w-full rounded-md border bg-card px-2 text-sm">
                    <option value="">Infer from profiles and models</option>
                    {domains.filter((d) => standard === "GDP" || d.standard !== "GDP" || d.domain_id === domainId)
                      .map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
                  </select>
                </div>
              </section>

              <section className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Target model</h4>
                  <div role="tablist" className="ml-auto inline-flex rounded-lg border bg-muted/40 p-0.5">
                    {([["existing", `Map to existing · ${existing.length}`], ["new", "New model"]] as const).map(([value, label]) => (
                      <button key={value} type="button" role="tab" aria-selected={mode === value} disabled={value === "existing" && !existing.length}
                              onClick={() => setMode(value)}
                              className={cn("rounded-md px-3 py-1 text-xs font-medium disabled:opacity-40", mode === value ? "bg-card shadow-sm" : "text-muted-foreground")}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {mode === "existing" && (
                  <div className="space-y-2">
                    {existing.map((s) => {
                      const on = picked.includes(s.fqn);
                      return (
                        <label key={s.fqn} className={cn("block cursor-pointer rounded-xl border p-3 transition", on ? "border-primary bg-primary/5 ring-1 ring-primary/30" : "hover:bg-muted/40")}>
                          <div className="flex items-start gap-3">
                            <input type="checkbox" className="mt-1" checked={on}
                                   onChange={() => setPicked((p) => (p.includes(s.fqn) ? p.filter((x) => x !== s.fqn) : [...p, s.fqn]))} />
                            <div className="min-w-0 flex-1 space-y-1.5">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-sm font-semibold">{s.target_table}</span>
                                {s.domain_name && <Badge variant="outline" className="text-[10px]">{s.domain_name}</Badge>}
                                <span className={cn("ml-auto rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
                                  s.score >= strong ? "bg-success/10 text-success" : s.score >= 0.35 ? "bg-warning/10 text-warning" : "bg-muted text-muted-foreground")}>
                                  {Math.round(s.score * 100)}% match
                                </span>
                              </div>
                              <p className="truncate font-mono text-[11px] text-muted-foreground">{s.fqn}</p>
                              {s.coverage_target != null && (
                                <div className="grid gap-2 sm:grid-cols-2">
                                  <Coverage label="Target columns covered" value={s.coverage_target} />
                                  <Coverage label="Source columns placed" value={s.coverage_source ?? 0} />
                                </div>
                              )}
                              <ul className="space-y-0.5 text-[11px] text-muted-foreground">
                                {(s.evidence?.length ? s.evidence : [s.reason]).filter(Boolean).map((e) => <li key={e}>• {e}</li>)}
                              </ul>
                              {(s.matched?.length || s.unmatched_source_columns?.length) ? (
                                <details className="text-[11px]">
                                  <summary className="cursor-pointer text-primary">Column match</summary>
                                  <div className="mt-1 grid gap-2 sm:grid-cols-2">
                                    <div>
                                      <p className="font-semibold">Matched</p>
                                      {(s.matched ?? []).map(([src, tgt, how]) => (
                                        <p key={src} className="font-mono">{src} → {tgt} <span className="text-muted-foreground">({how})</span></p>
                                      ))}
                                    </div>
                                    <div>
                                      {(s.unmatched_source_columns?.length ?? 0) > 0 && <p className="font-semibold">No place in the target yet</p>}
                                      {(s.unmatched_source_columns ?? []).map((c) => <p key={c} className="font-mono text-warning">{c}</p>)}
                                      {(s.missing_target_columns?.length ?? 0) > 0 && <p className="mt-1 font-semibold">Target columns not fed</p>}
                                      {(s.missing_target_columns ?? []).map((c) => <p key={c} className="font-mono text-muted-foreground">{c}</p>)}
                                    </div>
                                  </div>
                                </details>
                              ) : null}
                            </div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                )}
                {mode === "new" && (
                  <div className="space-y-2 rounded-xl border p-3">
                    <p className="text-xs text-muted-foreground">Starts from the source columns; design it with AI in Mapping, with your conventions or none.</p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <label className="space-y-1 text-xs">Model name
                        <Input value={proposedName} onChange={(e) => setProposedName(e.target.value)} className="font-mono"
                               placeholder={proposed?.target_table ?? "DIM_..."} />
                      </label>
                      <label className="space-y-1 text-xs">Target schema
                        <Input value={proposedSchema} onChange={(e) => setProposedSchema(e.target.value)} className="font-mono" />
                      </label>
                    </div>
                  </div>
                )}
                <label className="block space-y-1 text-xs">Run name
                  <Input id="model_run_name" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder={`${schema} modeling`} />
                </label>
              </section>

              {/* 2. What we are modeling, then the evidence behind the recommendation */}
              <section className="grid grid-cols-2 gap-2 rounded-xl border bg-muted/20 p-3 sm:grid-cols-3 lg:grid-cols-6">
                {[
                  ["Tables", data.tables.length],
                  ["Rows", data.tables.reduce((n, t) => n + (t.row_count ?? 0), 0).toLocaleString()],
                  ["Columns", data.tables.reduce((n, t) => n + (t.column_count ?? 0), 0)],
                  ["Avg quality", avgScore != null ? Math.round(avgScore) : "–"],
                  ["Key candidates", data.tables.reduce((n, t) => n + t.key_candidates.length, 0)],
                  ["PII columns", data.tables.reduce((n, t) => n + t.pii_columns.length, 0)],
                ].map(([k, v]) => (
                  <div key={String(k)}><p className="text-lg font-semibold tabular-nums">{v}</p><p className="text-[11px] text-muted-foreground">{k}</p></div>
                ))}
              </section>

              <details className="group rounded-xl border p-3" open={tables.length > 1}>
                <summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold">
                  <GitBranch className="h-4 w-4 text-primary" />Evidence: quality, relationships and the model graph
                  <span className="text-xs font-normal text-muted-foreground">from stored profiles; no data was read</span>
                </summary>
                <div className="mt-3 space-y-4">
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {data.tables.map((t) => (
                      <div key={t.table} className="rounded-lg border bg-card p-3">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium">{t.table}</span>
                          <span className="ml-auto"><GradeChip card={t.quality} /></span>
                        </div>
                        <p className="mt-1 text-[11px] text-muted-foreground">{t.row_count?.toLocaleString() ?? "—"} rows · {t.column_count ?? "—"} columns</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {t.key_candidates.slice(0, 2).map((k) => (
                            <Badge key={k} variant="success" className="text-[10px]"><KeyRound className="mr-0.5 h-3 w-3" />{k}</Badge>
                          ))}
                          {t.pii_columns.length > 0 && (
                            <Badge variant="destructive" className="text-[10px]"><ShieldAlert className="mr-0.5 h-3 w-3" />{t.pii_columns.length} PII</Badge>
                          )}
                          {!t.staged && <Badge variant="warning" className="text-[10px]">not staged</Badge>}
                        </div>
                      </div>
                    ))}
                  </div>
                  {data.relationships.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {tables.length > 1 ? "No key relationships found between these tables." : "Select two or more tables to discover relationships."}
                    </p>
                  ) : (
                    <ul className="space-y-1.5">
                      {data.relationships.map((j, i) => (
                        <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm">
                          <span className="font-mono">{j.left}.{j.keys[0].split("=")[0]}</span>
                          <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="font-mono">{j.right}.{j.keys[0].split("=").pop()}</span>
                          <Badge variant="outline">{j.cardinality}</Badge>
                          <span className={cn("ml-auto text-xs tabular-nums", j.confidence >= (data.bands ?? DEFAULT_BANDS).join_strong ? "text-success" : "text-warning")}>
                            {Math.round(j.confidence * 100)}% confidence
                          </span>
                          {j.evidence && <p className="w-full text-[11px] text-muted-foreground">{j.evidence.join(" · ")}</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                  {graph && <ModelEr graph={graph} runName={runName || `${schema} modeling`} />}
                </div>
              </details>
            </>
          )}
        </div>

        <footer className="flex flex-wrap items-center gap-3 border-t px-6 py-3">
          <p className="text-xs text-muted-foreground">
            The run reads these tables in place and reuses their staged profiles, then opens at mapping. Nothing is copied.
          </p>
          <Button className="ml-auto" disabled={!data || !standard || pending || (mode === "existing" && picked.length === 0)} onClick={create}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            {pending ? "Creating run…" : "Create modeling run"}
          </Button>
        </footer>
      </aside>
    </div>
  );
}

const REC_TONE: Record<string, string> = {
  MAP_EXISTING: "border-success/30 bg-success/5 text-success",
  EXTEND_EXISTING: "border-primary/30 bg-primary/5 text-primary",
  REVIEW: "border-warning/30 bg-warning/5 text-warning",
  NEW: "border-violet-300 bg-violet-50/60 text-violet-700 dark:bg-violet-950/30 dark:text-violet-300",
};

function Coverage({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="text-[11px]">
      <div className="flex justify-between text-muted-foreground"><span>{label}</span><span className="tabular-nums">{pct}%</span></div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", pct >= 70 ? "bg-success" : pct >= 40 ? "bg-warning" : "bg-muted-foreground/50")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
