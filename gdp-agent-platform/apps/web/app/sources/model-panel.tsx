"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, GitBranch, KeyRound, Layers, Loader2, ShieldAlert, Sparkles, X } from "lucide-react";
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

type Mode = "existing" | "new";
type Standard = "GDP" | "GENERIC";

/** Replaces the onboarding wizard: everything needed to start modeling a set of staged tables, on one panel. */
export function ModelPanel({ database, schema, tables, domains, onClose }: {
  database: string; schema: string; tables: string[]; domains: DomainRow[]; onClose: () => void;
}) {
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
      const strong = r.data.models.suggestions.filter((s) => s.kind === "existing" && s.score >= (r.data.bands ?? DEFAULT_BANDS).model_match_strong).map((s) => s.fqn);
      if (strong.length) { setMode("existing"); setPicked(strong); }
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

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {!data && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Reading staged profiles, inferring relationships and matching models…
            </p>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

          {data && (
            <>
              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-primary" />
                  <h4 className="text-sm font-semibold">Data quality</h4>
                  {avgScore != null && <span className="text-xs text-muted-foreground">average {Math.round(avgScore)}</span>}
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {data.tables.map((t) => (
                    <div key={t.table} className="rounded-lg border bg-card p-3">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{t.table}</span>
                        <span className="ml-auto"><GradeChip card={t.quality} /></span>
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {t.row_count?.toLocaleString() ?? "—"} rows · {t.column_count ?? "—"} columns
                      </p>
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
              </section>

              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <GitBranch className="h-4 w-4 text-primary" />
                  <h4 className="text-sm font-semibold">Relationships</h4>
                  <span className="text-xs text-muted-foreground">inferred from stored profiles; no data was read</span>
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
              </section>

              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <GitBranch className="h-4 w-4 text-primary" />
                  <h4 className="text-sm font-semibold">Is this source part of GDP?</h4>
                </div>
                <div role="radiogroup" aria-label="Modeling standard" className="grid gap-2 sm:grid-cols-2">
                  {([
                    ["GDP", "Yes, GDP", "Hub and spoke model with GDP keys, audit columns, prefix and GDP knowledge packs"],
                    ["GENERIC", "No, another source", "Plain model with no GDP conventions; GDP packs are not used"],
                  ] as const).map(([value, title, body]) => (
                    <button key={value} type="button" role="radio" aria-checked={standard === value}
                            onClick={() => setStandard(value)}
                            className={cn("rounded-xl border p-3 text-left",
                              standard === value ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:bg-muted/40")}>
                      <p className="text-sm font-semibold">{title}</p>
                      <p className="text-xs text-muted-foreground">{body}</p>
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  {data.suggested_standard === "GDP"
                    ? "Preselected because a GDP pack matched these tables. Change it if this source is not GDP."
                    : "No GDP pack matched these tables, so this defaults to another source. Choose GDP if it belongs there."}
                </p>
              </section>

              <section className="space-y-3">
                <div className="flex items-center gap-2">
                  <Layers className="h-4 w-4 text-primary" />
                  <h4 className="text-sm font-semibold">Target model</h4>
                </div>
                {data.domain?.detected ? (
                  <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
                    <p>
                      <span className="font-semibold">{data.domain.detected.domain_name}</span> domain detected
                      <span className="ml-2 text-xs text-muted-foreground">{Math.round(data.domain.detected.confidence * 100)}% confidence</span>
                    </p>
                    {data.domain.detected.signals.length > 0 && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Signals: {data.domain.detected.signals.join(", ")}. Its contract drives the mapping, STTM and dbt generation.
                      </p>
                    )}
                    {data.domain.candidates.length > 1 && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Also considered: {data.domain.candidates.slice(1).map((c) => `${c.domain_name} ${Math.round(c.confidence * 100)}%`).join(", ")}
                      </p>
                    )}
                  </div>
                ) : data.domain && (
                  <p className="text-xs text-muted-foreground">No domain contract matched these tables with enough confidence; pick a pack below or propose a new model.</p>
                )}
                <div className="grid gap-2 sm:grid-cols-2">
                  {([
                    ["existing", "Map to existing models", existing.length ? `${existing.length} matching model${existing.length === 1 ? "" : "s"} found` : "No matching model found"],
                    ["new", "Propose a new model", "Starts from the source columns; design it with AI in Mapping, your conventions or none"],
                  ] as const).map(([value, title, body]) => (
                    <button key={value} type="button" aria-pressed={mode === value} disabled={value === "existing" && !existing.length}
                            onClick={() => setMode(value)}
                            className={cn("rounded-xl border p-3 text-left disabled:opacity-50",
                              mode === value ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:bg-muted/40")}>
                      <p className="text-sm font-semibold">{title}</p>
                      <p className="text-xs text-muted-foreground">{body}</p>
                    </button>
                  ))}
                </div>
                {mode === "new" && (
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="space-y-1 text-xs">Model name
                      <Input value={proposedName} onChange={(e) => setProposedName(e.target.value)} className="font-mono"
                             placeholder={proposed?.target_table ?? "DIM_..."} />
                    </label>
                    <label className="space-y-1 text-xs">Target schema
                      <Input value={proposedSchema} onChange={(e) => setProposedSchema(e.target.value)} className="font-mono" />
                    </label>
                  </div>
                )}
                {mode === "existing" && (
                  <div className="space-y-1.5">
                    {existing.map((s) => (
                      <label key={s.fqn} className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 hover:bg-muted/40">
                        <input type="checkbox" className="mt-1" checked={picked.includes(s.fqn)}
                               onChange={() => setPicked((p) => (p.includes(s.fqn) ? p.filter((x) => x !== s.fqn) : [...p, s.fqn]))} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="text-sm font-medium">{s.target_table}</span>
                            {s.domain_name && <span className="text-xs text-muted-foreground">{s.domain_name}</span>}
                            <Badge variant="outline" className="ml-auto">{Math.round(s.score * 100)}% match</Badge>
                          </span>
                          <span className="block truncate font-mono text-[11px] text-muted-foreground">{s.fqn}</span>
                          <span className="block text-xs text-muted-foreground">{s.reason}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap items-end gap-3">
                  <div>
                    <label htmlFor="model_domain" className="mb-1 block text-xs font-medium">Domain knowledge pack</label>
                    <select id="model_domain" value={domainId} onChange={(e) => setDomainId(e.target.value)}
                            className="h-9 w-64 rounded-md border bg-card px-2 text-sm">
                      <option value="">Infer from profiles and models</option>
                      {domains.filter((d) => standard === "GDP" || d.standard !== "GDP" || d.domain_id === domainId)
                        .map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
                    </select>
                  </div>
                  <div className="flex-1">
                    <label htmlFor="model_run_name" className="mb-1 block text-xs font-medium">Run name</label>
                    <Input id="model_run_name" value={runName} onChange={(e) => setRunName(e.target.value)} placeholder={`${schema} modeling`} />
                  </div>
                </div>
              </section>
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
