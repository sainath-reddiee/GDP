"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import {
  ArrowRight, Columns3, Database, Download, FileCode2, Filter, GitBranch, GitCompareArrows, History, Loader2, Plus, RotateCcw,
  Search, Send, Sparkles, Trash2, Undo2, Wand2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { SttmContext, SttmDiff, SttmEntry, SttmFilter, SttmLine, SttmVersion } from "@/lib/hub-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Drawer } from "@/components/hub/overlay";
import { StatusPill, explain } from "@/components/hub/states";
import { SqlEditor } from "@/components/quality/sql-editor";
import { JoinPlanEditor, draftsFrom, joinPayload, type JoinDraft, type JoinGraph } from "@/components/sttm/join-plan-editor";
import { useToast } from "@/components/ui/toast";
import { compare, refineLine, submitChange, type SttmEdit } from "../actions";

const MAPPING_TYPES = ["DIRECT", "TRANSFORM", "DERIVED", "LOOKUP", "CONSTANT", "UNMAPPED"];
const FIELDS: (keyof SttmLine)[] = ["source_system", "source_table", "source_column", "source_datatype", "target_datatype", "mapping_type",
  "transformation", "filter_rule", "business_rule", "business_definition"];
type Tab = "columns" | "joins" | "sql" | "evidence" | "versions";
type Design = { grain: string; business_keys: string[]; dedup: string; scd_type: string; incremental_strategy: string; filters: SttmFilter[] };

function designOf(entry: SttmEntry): Design {
  const d = (entry.table_design ?? {}) as Record<string, unknown>;
  return {
    grain: String(d.grain ?? ""), business_keys: (d.business_keys as string[]) ?? [], dedup: String(d.dedup ?? ""),
    scd_type: String(d.scd_type ?? "1"), incremental_strategy: String(d.incremental_strategy ?? "merge"),
    filters: ((d.filters as SttmFilter[]) ?? []).map((f) => ({ table: f.table ?? null, condition: f.condition, reason: f.reason ?? null })),
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// ---------------------------------------------------------------- line drawer

function LineDrawer({ catalogId, line, original, isNew, context, canEdit, onClose, onStage }: {
  catalogId: string; line: SttmLine; original: SttmLine | null; isNew: boolean; context: SttmContext; canEdit: boolean;
  onClose: () => void; onStage: (l: SttmLine) => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<SttmLine>(line);
  const [prompt, setPrompt] = useState("");
  const [proposal, setProposal] = useState<{ transformation: string; rationale: string | null } | null>(null);
  const [pending, start] = useTransition();
  const set = (patch: Partial<SttmLine>) => setDraft((d) => ({ ...d, ...patch }));
  const table = (draft.source_table ?? "").toUpperCase();
  const profile = table && draft.source_column ? context.profiles[`${table}.${String(draft.source_column).toUpperCase()}`] : undefined;
  const candidates = context.candidates.filter((c) => c.target_column === draft.target_column.toUpperCase());
  const decision = context.decisions.find((d) => d.target_column === draft.target_column.toUpperCase());
  const propose = () => start(async () => {
    const r = await refineLine(catalogId, { target_column: draft.target_column, prompt, source_table: draft.source_table, source_column: draft.source_column,
      source_datatype: draft.source_datatype, target_datatype: draft.target_datatype, current_transformation: draft.transformation,
      business_definition: draft.business_definition });
    if (r.ok) setProposal({ transformation: r.data.transformation, rationale: r.data.rationale });
    else toast("error", explain(r.error).message);
  });
  const changed = FIELDS.filter((f) => !same(draft[f], original?.[f]));
  return (
    <Drawer open onClose={onClose} wide title={<span className="font-mono">{draft.target_column || "New column"}</span>}
            subtitle={isNew ? "New target column" : changed.length ? `${changed.length} field${changed.length === 1 ? "" : "s"} changed` : "Every field can change, even on an approved mapping"}
            footer={canEdit ? <>
              <Button variant="ghost" onClick={onClose}>Cancel</Button>
              <Button onClick={() => onStage(draft)} disabled={!draft.target_column.trim() || (!isNew && !changed.length)} data-testid="stage-line">
                Stage change
              </Button>
            </> : undefined}>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-3">
          {isNew && (
            <div><Label htmlFor="ln-target">Target column</Label>
              <Input id="ln-target" className="font-mono" value={draft.target_column} onChange={(e) => set({ target_column: e.target.value.toUpperCase() })} /></div>
          )}
          <div className="grid gap-x-3 sm:grid-cols-3">
            <div><Label htmlFor="ln-sys">Source system</Label>
              <Input id="ln-sys" className="font-mono" disabled={!canEdit} value={draft.source_system ?? ""} onChange={(e) => set({ source_system: e.target.value.toUpperCase() || null })} /></div>
            <div><Label htmlFor="ln-table">Source table</Label>
              <Input id="ln-table" list="ln-tables" className="font-mono" disabled={!canEdit} value={draft.source_table ?? ""}
                     onChange={(e) => set({ source_table: e.target.value.toUpperCase() || null })} />
              <datalist id="ln-tables">{context.tables.concat(Object.keys(context.columns)).filter((t, i, a) => a.indexOf(t) === i).map((t) => <option key={t} value={t} />)}</datalist></div>
            <div><Label htmlFor="ln-col">Source column</Label>
              <Input id="ln-col" list="ln-cols" className="font-mono" disabled={!canEdit} value={draft.source_column ?? ""}
                     onChange={(e) => {
                       const col = e.target.value.toUpperCase();
                       const known = (context.columns[table] ?? []).find((c) => c.column === col);
                       set({ source_column: col || null, source_datatype: known?.data_type ?? draft.source_datatype });
                     }} />
              <datalist id="ln-cols">{(context.columns[table] ?? []).map((c) => <option key={c.column} value={c.column}>{c.data_type ?? ""}</option>)}</datalist></div>
          </div>
          <div className="grid gap-x-3 sm:grid-cols-3">
            <div><Label htmlFor="ln-sdt">Source type</Label>
              <Input id="ln-sdt" className="font-mono" disabled={!canEdit} value={draft.source_datatype ?? ""} onChange={(e) => set({ source_datatype: e.target.value.toUpperCase() || null })} /></div>
            <div><Label htmlFor="ln-tdt">Target type</Label>
              <Input id="ln-tdt" className="font-mono" disabled={!canEdit} value={draft.target_datatype ?? ""} onChange={(e) => set({ target_datatype: e.target.value.toUpperCase() || null })} /></div>
            <div><Label htmlFor="ln-mt">Mapping type</Label>
              <Select id="ln-mt" disabled={!canEdit} value={draft.mapping_type ?? "DIRECT"} onChange={(e) => set({ mapping_type: e.target.value })}>
                {MAPPING_TYPES.map((t) => <option key={t} value={t}>{t.toLowerCase()}</option>)}
              </Select></div>
          </div>
          <div>
            <Label>Transformation (one SQL expression)</Label>
            <SqlEditor value={draft.transformation ?? ""} readOnly={!canEdit} rows={4} label="Transformation" onChange={(v) => set({ transformation: v || null })} />
          </div>
          {canEdit && context.run_id && (
            <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-3">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-blue-800"><Sparkles className="h-3.5 w-3.5" /> Propose SQL with AI</p>
              <div className="mt-2 flex gap-2">
                <Input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="e.g. proper-case the name and trim spaces" className="h-8 text-xs" />
                <Button size="sm" variant="outline" disabled={pending || prompt.trim().length < 3} onClick={propose}>
                  {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />} Propose
                </Button>
              </div>
              {proposal && (
                <div className="mt-2 space-y-1.5">
                  <pre className="overflow-x-auto rounded-lg bg-slate-950 p-2 font-mono text-xs text-emerald-300">{proposal.transformation}</pre>
                  {proposal.rationale && <p className="text-xs text-blue-900/80">{proposal.rationale}</p>}
                  <Button size="sm" onClick={() => { set({ transformation: proposal.transformation, mapping_type: draft.mapping_type === "DIRECT" ? "TRANSFORM" : draft.mapping_type }); setProposal(null); }}>Use this</Button>
                </div>
              )}
            </div>
          )}
          <div>
            <Label htmlFor="ln-filter">Column filter <span className="font-normal text-muted-foreground">(keep the value only when this holds, else NULL)</span></Label>
            <Input id="ln-filter" className="font-mono" disabled={!canEdit} value={draft.filter_rule ?? ""} placeholder="STATUS <> 'DELETED'"
                   onChange={(e) => set({ filter_rule: e.target.value || null })} />
          </div>
          <div><Label htmlFor="ln-rule">Business rule</Label>
            <Input id="ln-rule" disabled={!canEdit} value={draft.business_rule ?? ""} onChange={(e) => set({ business_rule: e.target.value || null })} /></div>
          <div><Label htmlFor="ln-def">Business definition</Label>
            <Textarea id="ln-def" disabled={!canEdit} className="min-h-14" value={draft.business_definition ?? ""} onChange={(e) => set({ business_definition: e.target.value || null })} /></div>
        </div>
        <aside className="space-y-3">
          <section className="rounded-xl border border-border/80 p-3 text-xs">
            <p className="mb-2 flex items-center gap-1.5 font-semibold"><Database className="h-3.5 w-3.5" /> Source column profile</p>
            {!profile ? <p className="text-muted-foreground">No profile for {table && draft.source_column ? `${table}.${draft.source_column}` : "this source"} yet.</p> : (
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1">
                <dt className="text-muted-foreground">Type</dt><dd className="font-mono">{profile.data_type ?? "-"}</dd>
                <dt className="text-muted-foreground">Semantic</dt><dd>{profile.semantic_type ?? "-"}</dd>
                <dt className="text-muted-foreground">Nulls</dt><dd className="tabular-nums">{profile.null_pct == null ? "-" : `${Number(profile.null_pct).toFixed(1)}%`}</dd>
                <dt className="text-muted-foreground">Distinct</dt><dd className="tabular-nums">{profile.distinct_count?.toLocaleString() ?? "-"}</dd>
                <dt className="text-muted-foreground">Cardinality</dt><dd>{profile.cardinality ?? "-"}</dd>
                <dt className="text-muted-foreground">PII</dt><dd>{profile.pii_classification ?? "-"}</dd>
                {profile.min_value != null && <><dt className="text-muted-foreground">Range</dt><dd className="truncate font-mono">{profile.min_value} .. {profile.max_value}</dd></>}
              </dl>
            )}
          </section>
          <section className="rounded-xl border border-border/80 p-3 text-xs">
            <p className="mb-2 font-semibold">Mapping candidates</p>
            {!candidates.length ? <p className="text-muted-foreground">No ranked candidates recorded for this column.</p> : (
              <ul className="space-y-1.5">
                {candidates.slice(0, 5).map((c) => (
                  <li key={`${c.source_table}.${c.source_column}`} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono" title={c.generated_reason ?? undefined}>{c.source_table}.{c.source_column}</span>
                    <span className="tabular-nums text-muted-foreground">{Math.round((c.confidence ?? c.final_score) * 100)}%</span>
                    {canEdit && (
                      <button type="button" className="rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted"
                              onClick={() => set({ source_table: c.source_table, source_column: c.source_column, source_datatype: c.source_datatype })}>Use</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {decision && (
              <p className="mt-2 border-t border-border/60 pt-2 text-muted-foreground">
                Run decision: <b>{decision.decision.toLowerCase()}</b>{decision.reviewer ? ` by ${decision.reviewer}` : ""}
                {decision.business_justification ? ` · ${decision.business_justification}` : ""}
              </p>
            )}
          </section>
        </aside>
      </div>
    </Drawer>
  );
}

// ---------------------------------------------------------------- workbench

export function SttmWorkbench({ entry, versions, latest, context, graph, previewSql, canEdit }: {
  entry: SttmEntry; versions: SttmVersion[]; latest: number; context: SttmContext; graph: JoinGraph; previewSql: string; canEdit: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const editable = canEdit && entry.version === latest;
  const original = useMemo(() => new Map(entry.lines.map((l) => [l.target_column.toUpperCase(), l])), [entry.lines]);
  const baseDesign = useMemo(() => designOf(entry), [entry]);
  const tables = useMemo(() => Array.from(new Set([...context.tables, ...Object.keys(context.columns),
    ...(graph.driving_table ? [graph.driving_table] : []), ...graph.joins.map((j) => j.right_table)])).filter(Boolean).sort(), [context, graph]);

  const [tab, setTab] = useState<Tab>("columns");
  const [edited, setEdited] = useState<Record<string, SttmLine>>({});
  const [added, setAdded] = useState<SttmLine[]>([]);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [design, setDesign] = useState<Design>(baseDesign);
  const [driving, setDriving] = useState(graph.driving_table ?? tables[0] ?? "");
  const [drafts, setDrafts] = useState<JoinDraft[]>(() => draftsFrom(graph));
  const [open, setOpen] = useState<{ line: SttmLine; isNew: boolean } | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "logic" | "unmapped" | "changed" | "filtered">("all");
  const [note, setNote] = useState("");
  const [pending, start] = useTransition();
  const [diff, setDiff] = useState<SttmDiff | null>(null);
  const [from, setFrom] = useState(versions[1]?.version ?? entry.version);
  const [to, setTo] = useState(entry.version);

  const joinsChanged = driving !== (graph.driving_table ?? "") || !same(drafts, draftsFrom(graph));
  const designChanged = !same(design, baseDesign);
  const lineEdits: SttmEdit[] = Object.values(edited).map((l) => {
    const o = original.get(l.target_column.toUpperCase());
    const e: SttmEdit = { target_column: l.target_column };
    for (const f of FIELDS) if (!same(l[f], o?.[f])) (e as Record<string, unknown>)[f] = l[f] ?? null;
    return e;
  }).filter((e) => Object.keys(e).length > 1);
  const count = lineEdits.length + added.length + removed.size + (designChanged ? 1 : 0) + (joinsChanged ? 1 : 0);

  const rows: (SttmLine & { _state?: "new" | "edited" | "removed" })[] = [
    ...entry.lines.map((l) => {
      const key = l.target_column.toUpperCase();
      if (removed.has(key)) return { ...l, _state: "removed" as const };
      return edited[key] ? { ...edited[key], _state: "edited" as const } : l;
    }),
    ...added.map((l) => ({ ...l, _state: "new" as const })),
  ];
  const term = q.trim().toLowerCase();
  const shown = rows.filter((l) => (!term || `${l.target_column} ${l.source_table ?? ""} ${l.source_column ?? ""} ${l.transformation ?? ""}`.toLowerCase().includes(term))
    && (filter === "all" || (filter === "logic" && !!l.transformation) || (filter === "unmapped" && (l.mapping_type === "UNMAPPED" || !l.source_column))
      || (filter === "changed" && !!l._state) || (filter === "filtered" && !!l.filter_rule)));

  const stage = (l: SttmLine, isNew: boolean) => {
    const key = l.target_column.toUpperCase();
    if (isNew) setAdded((a) => [...a.filter((x) => x.target_column.toUpperCase() !== key), { ...l, target_column: key }]);
    else setEdited((e) => ({ ...e, [key]: l }));
    setOpen(null);
  };
  const discard = () => { setEdited({}); setAdded([]); setRemoved(new Set()); setDesign(baseDesign); setDriving(graph.driving_table ?? ""); setDrafts(draftsFrom(graph)); setNote(""); };
  const submit = () => start(async () => {
    const r = await submitChange(entry.catalog_id, {
      base_version: entry.version, note: note.trim() || undefined,
      edits: lineEdits.length ? lineEdits : undefined,
      add_lines: added.length ? added.map(({ join_logic: _j, ...l }) => l) : undefined,
      remove_lines: removed.size ? Array.from(removed) : undefined,
      design: designChanged ? { grain: design.grain || null, business_keys: design.business_keys, dedup: design.dedup || null,
        scd_type: design.scd_type, incremental_strategy: design.incremental_strategy, filters: design.filters } : undefined,
      joins: joinsChanged ? joinPayload(driving, drafts) : undefined,
    });
    if (r.ok) { toast("ok", `Saved as v${r.data.version}${r.data.run_updated ? " and written back to the run" : ""}`); discard(); router.refresh(); }
    else if (r.pending) { toast("info", r.error); discard(); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const runCompare = () => start(async () => { const r = await compare(entry.catalog_id, from, to); if (r.ok) setDiff(r.data); });

  const tabs: [Tab, string, typeof Columns3][] = [["columns", `Columns (${rows.length - removed.size})`, Columns3], ["joins", "Joins, filters and grain", GitBranch],
    ["sql", "SQL preview", FileCode2], ["evidence", "Mapping evidence", Sparkles], ["versions", `Versions (${versions.length})`, History]];

  return (
    <div className="space-y-4">
      {!editable && canEdit && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Viewing v{entry.version}. Changes start from the latest version, v{latest}.{" "}
          <button className="font-medium underline" onClick={() => router.push(`/dashboard/sttm/${entry.catalog_id}`)}>Open v{latest}</button>
        </p>
      )}
      <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Mapping sections">
        {tabs.map(([id, label, Icon]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined} data-testid={`sttm-tab-${id}`}
                  className={cn("relative flex items-center gap-1.5 px-3 pb-2 pt-1 text-sm transition", tab === id ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" /> {label}
            {tab === id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-2 pb-2 text-xs">
          <a href={`/bff/sttm-export/${entry.catalog_id}?version=${entry.version}`} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 font-medium text-primary hover:bg-primary/10"><Download className="h-3.5 w-3.5" /> dbt</a>
          <a href={`/bff/sttm-export/${entry.catalog_id}?version=${entry.version}&format=csv`} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 font-medium text-primary hover:bg-primary/10"><Download className="h-3.5 w-3.5" /> CSV</a>
        </span>
      </nav>

      {tab === "columns" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter columns, sources or logic" className="pl-9" aria-label="Filter columns" />
            </div>
            <div className="flex rounded-xl bg-muted p-1 text-xs">
              {(["all", "logic", "unmapped", "filtered", "changed"] as const).map((f) => (
                <button key={f} type="button" onClick={() => setFilter(f)}
                        className={cn("rounded-lg px-2.5 py-1 font-medium capitalize transition", filter === f ? "bg-card shadow-xs" : "text-muted-foreground")}>
                  {f === "logic" ? "with logic" : f}
                </button>
              ))}
            </div>
            {editable && (
              <Button variant="outline" onClick={() => setOpen({ isNew: true, line: { target_column: "", target_datatype: "VARCHAR", mapping_type: "DIRECT", source_table: null, source_column: null, transformation: null, business_rule: null, business_definition: null } })}
                      data-testid="add-column">
                <Plus className="h-4 w-4" /> Add column
              </Button>
            )}
          </div>
          <div className="surface overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/60 text-left text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
                <tr><th className="px-3 py-2">Target column</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2">Transformation and filter</th><th className="px-3 py-2">Business rule</th><th className="px-3 py-2" /></tr>
              </thead>
              <tbody>
                {shown.map((l) => {
                  const key = l.target_column.toUpperCase();
                  return (
                    <tr key={key} className={cn("border-t border-border/60 align-top transition hover:bg-muted/30",
                      l._state === "removed" && "opacity-50 line-through", l._state === "new" && "bg-emerald-50/50", l._state === "edited" && "bg-amber-50/50")}>
                      <td className="px-3 py-2">
                        <button type="button" onClick={() => setOpen({ line: l, isNew: l._state === "new" })} className="font-mono font-medium text-primary hover:underline" data-testid={`line-${key}`}>{l.target_column}</button>
                        <span className="block text-xs text-muted-foreground">{l.target_datatype ?? "-"}</span>
                        {l._state && <span className="mt-0.5 inline-block rounded-full bg-muted px-1.5 text-[10px] font-medium">{l._state}</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{l.source_table ? `${l.source_table}.${l.source_column ?? "?"}` : <span className="text-muted-foreground">none</span>}</td>
                      <td className="px-3 py-2 text-xs">{(l.mapping_type ?? "").toLowerCase()}</td>
                      <td className="max-w-[340px] px-3 py-2">
                        <code className="block truncate font-mono text-xs" title={l.transformation ?? undefined}>{l.transformation ?? <span className="text-muted-foreground">as is</span>}</code>
                        {l.filter_rule && <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-blue-700"><Filter className="h-3 w-3" />{l.filter_rule}</span>}
                      </td>
                      <td className="max-w-[220px] truncate px-3 py-2 text-xs text-muted-foreground" title={l.business_rule ?? undefined}>{l.business_rule ?? "-"}</td>
                      <td className="px-3 py-2 text-right">
                        {editable && l._state !== "new" && (
                          <button type="button" aria-label={l._state === "removed" ? `Keep ${key}` : `Remove ${key}`}
                                  onClick={() => setRemoved((r) => { const n = new Set(r); if (n.has(key)) n.delete(key); else n.add(key); return n; })}
                                  className="rounded p-1 text-muted-foreground hover:bg-muted">
                            {l._state === "removed" ? <Undo2 className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
                          </button>
                        )}
                        {editable && l._state === "new" && (
                          <button type="button" aria-label={`Drop new ${key}`} onClick={() => setAdded((a) => a.filter((x) => x.target_column.toUpperCase() !== key))}
                                  className="rounded p-1 text-muted-foreground hover:bg-muted"><Trash2 className="h-3.5 w-3.5" /></button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {!shown.length && <tr><td colSpan={6} className="px-3 py-8 text-center text-sm text-muted-foreground">No column matches.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "joins" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <section className="surface space-y-3 p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold"><GitBranch className="h-4 w-4 text-primary" /> Join plan</h3>
            {tables.length ? (
              <JoinPlanEditor graph={graph} tables={tables} canEdit={editable} driving={driving} drafts={drafts} onDriving={setDriving} onDrafts={setDrafts} />
            ) : <p className="text-sm text-muted-foreground">No source tables recorded for this mapping.</p>}
          </section>
          <div className="space-y-4">
            <section className="surface space-y-3 p-4" data-testid="filters-editor">
              <h3 className="flex items-center gap-2 text-sm font-semibold"><Filter className="h-4 w-4 text-primary" /> Row filters</h3>
              <p className="text-xs text-muted-foreground">Only source rows matching every filter load. Reference columns as TABLE.COLUMN.</p>
              {design.filters.map((f, i) => (
                <div key={i} className="space-y-1.5 rounded-xl border border-border/80 p-2.5">
                  <div className="flex gap-2">
                    <Select aria-label="Filter table" className="h-8 w-40 text-xs" disabled={!editable} value={f.table ?? ""}
                            onChange={(e) => setDesign((d) => ({ ...d, filters: d.filters.map((x, k) => (k === i ? { ...x, table: e.target.value || null } : x)) }))}>
                      <option value="">any table</option>
                      {tables.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                    <Input aria-label="Filter condition" className="h-8 font-mono text-xs" disabled={!editable} value={f.condition} placeholder="ORDERS.STATUS <> 'CANCELLED'"
                           onChange={(e) => setDesign((d) => ({ ...d, filters: d.filters.map((x, k) => (k === i ? { ...x, condition: e.target.value } : x)) }))} />
                    {editable && <button type="button" aria-label="Remove filter" className="rounded p-1 text-muted-foreground hover:bg-muted"
                                         onClick={() => setDesign((d) => ({ ...d, filters: d.filters.filter((_, k) => k !== i) }))}><Trash2 className="h-3.5 w-3.5" /></button>}
                  </div>
                  <Input aria-label="Filter reason" className="h-7 text-xs" disabled={!editable} value={f.reason ?? ""} placeholder="Why (optional)"
                         onChange={(e) => setDesign((d) => ({ ...d, filters: d.filters.map((x, k) => (k === i ? { ...x, reason: e.target.value || null } : x)) }))} />
                </div>
              ))}
              {editable && <Button size="sm" variant="outline" onClick={() => setDesign((d) => ({ ...d, filters: [...d.filters, { table: driving || null, condition: "", reason: null }] }))}>
                <Plus className="h-3.5 w-3.5" /> Add filter</Button>}
            </section>
            <section className="surface space-y-1 p-4">
              <h3 className="text-sm font-semibold">Grain, keys and loading</h3>
              <Label htmlFor="d-grain">Grain</Label>
              <Input id="d-grain" disabled={!editable} value={design.grain} onChange={(e) => setDesign((d) => ({ ...d, grain: e.target.value }))} placeholder="One row per customer" />
              <Label htmlFor="d-keys">Business keys</Label>
              <Input id="d-keys" className="font-mono" disabled={!editable} value={design.business_keys.join(", ")}
                     onChange={(e) => setDesign((d) => ({ ...d, business_keys: e.target.value.toUpperCase().split(/[,\s]+/).filter(Boolean) }))} placeholder="CUSTOMER_ID" />
              <Label htmlFor="d-dedup">Dedup rule</Label>
              <Input id="d-dedup" disabled={!editable} value={design.dedup} onChange={(e) => setDesign((d) => ({ ...d, dedup: e.target.value }))} placeholder="keep the latest row per key" />
              <div className="grid grid-cols-2 gap-x-3">
                <div><Label htmlFor="d-scd">SCD type</Label>
                  <Select id="d-scd" disabled={!editable} value={design.scd_type} onChange={(e) => setDesign((d) => ({ ...d, scd_type: e.target.value }))}>
                    <option value="0">Type 0 (fixed)</option><option value="1">Type 1 (overwrite)</option><option value="2">Type 2 (history)</option>
                  </Select></div>
                <div><Label htmlFor="d-strat">Load strategy</Label>
                  <Select id="d-strat" disabled={!editable} value={design.incremental_strategy} onChange={(e) => setDesign((d) => ({ ...d, incremental_strategy: e.target.value }))}>
                    {["merge", "append", "delete+insert", "insert_overwrite", "full_refresh"].map((s) => <option key={s} value={s}>{s}</option>)}
                  </Select></div>
              </div>
            </section>
          </div>
        </div>
      )}

      {tab === "sql" && (
        <section className="surface space-y-2 p-4">
          <p className="text-xs text-muted-foreground">The model SQL of v{entry.version}: driving table, joins, row filters and every column. Staged changes appear after they are approved.</p>
          <SqlEditor value={previewSql} readOnly rows={Math.min(30, previewSql.split("\n").length + 1)} label="Model SQL" />
        </section>
      )}

      {tab === "evidence" && (
        <section className="surface overflow-x-auto p-0">
          {!context.run_id ? <p className="p-4 text-sm text-muted-foreground">This mapping has no producing run, so there is no mapping evidence.</p> : (
            <table className="w-full text-sm">
              <thead className="bg-muted/60 text-left text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
                <tr><th className="px-3 py-2">Target</th><th className="px-3 py-2">Decision</th><th className="px-3 py-2">Top candidates</th><th className="px-3 py-2">Justification</th></tr>
              </thead>
              <tbody>
                {entry.lines.map((l) => {
                  const key = l.target_column.toUpperCase();
                  const d = context.decisions.find((x) => x.target_column === key);
                  const cs = context.candidates.filter((c) => c.target_column === key).slice(0, 3);
                  return (
                    <tr key={key} className="border-t border-border/60 align-top">
                      <td className="px-3 py-2 font-mono text-xs font-medium">{key}</td>
                      <td className="px-3 py-2">{d ? <StatusPill status={d.decision === "MODIFIED" ? "APPROVED" : d.decision} /> : <span className="text-xs text-muted-foreground">none</span>}</td>
                      <td className="px-3 py-2 text-xs">{cs.length ? cs.map((c) => (
                        <span key={`${c.source_table}.${c.source_column}`} className="mr-2 inline-flex items-center gap-1 font-mono">{c.source_table}.{c.source_column}
                          <span className="text-muted-foreground">{Math.round((c.confidence ?? c.final_score) * 100)}%</span></span>)) : <span className="text-muted-foreground">none</span>}</td>
                      <td className="max-w-[320px] px-3 py-2 text-xs text-muted-foreground">{d?.business_justification ?? "-"}{d?.reviewer ? ` (${d.reviewer})` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "versions" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <ol className="space-y-2">
            {versions.map((v) => (
              <li key={v.version}>
                <button onClick={() => router.push(`/dashboard/sttm/${entry.catalog_id}?version=${v.version}`)}
                        className={cn("w-full rounded-lg border px-3 py-2 text-left text-xs transition", v.version === entry.version ? "border-primary/40 bg-accent" : "border-border/70 hover:bg-muted/50")}>
                  <span className="font-semibold">v{v.version}</span> <StatusPill status={v.status} className="ml-1" />
                  <span className="ml-2 text-muted-foreground">{v.origin.toLowerCase().replace(/_/g, " ")}</span>
                  <span className="block truncate text-muted-foreground">{v.change_note ?? `${v.line_count} lines`}</span>
                  <span className="block text-[10px] text-muted-foreground">{v.created_by} · {v.created_at.slice(0, 16)}</span>
                </button>
              </li>
            ))}
          </ol>
          <section className="surface space-y-3 p-4">
            <div className="flex items-center gap-2">
              <Select aria-label="From version" className="h-8 w-auto text-xs" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
              <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              <Select aria-label="To version" className="h-8 w-auto text-xs" value={to} onChange={(e) => setTo(Number(e.target.value))}>
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
              <Button size="sm" variant="outline" onClick={runCompare} disabled={pending || from === to}><GitCompareArrows className="h-3.5 w-3.5" /> Compare</Button>
            </div>
            {diff && (
              <div className="space-y-2 text-xs">
                <p className="font-medium">{diff.summary}</p>
                {diff.added.map((l) => <p key={`a${l.target_column}`} className="rounded bg-emerald-50 px-2 py-1 text-emerald-800">+ {l.target_column}</p>)}
                {diff.removed.map((l) => <p key={`r${l.target_column}`} className="rounded bg-rose-50 px-2 py-1 text-rose-800">- {l.target_column}</p>)}
                {diff.changed.map((c) => (
                  <div key={`c${c.target_column}`} className="rounded border border-border/70 p-2">
                    <p className="font-mono font-medium">{c.target_column}</p>
                    {c.fields.map((f) => (
                      <div key={f} className="mt-1">
                        <p className="text-[10px] uppercase text-muted-foreground">{f.replace(/_/g, " ")}</p>
                        <p className="break-all rounded bg-rose-50 px-1.5 py-0.5 font-mono text-rose-800">{String(c.from[f] ?? "none")}</p>
                        <p className="mt-0.5 break-all rounded bg-emerald-50 px-1.5 py-0.5 font-mono text-emerald-800">{String(c.to[f] ?? "none")}</p>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {editable && count > 0 && (
        <div className="sticky bottom-4 z-30 mx-auto flex w-full max-w-3xl flex-wrap items-center gap-2 rounded-2xl border bg-card p-2.5 shadow-pop" data-testid="change-bar">
          <span className="flex items-center gap-2 pl-2 text-sm font-medium"><span className="h-2 w-2 rounded-full bg-warning" />{count} change{count === 1 ? "" : "s"} staged</span>
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What and why (shown to the approver)" className="h-8 min-w-[200px] flex-1 text-xs" aria-label="Change note" />
          <Button size="sm" variant="ghost" onClick={discard}><RotateCcw className="h-3.5 w-3.5" /> Discard</Button>
          <Button size="sm" onClick={submit} disabled={pending || note.trim().length < 3} data-testid="submit-change">
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />} Submit for approval
          </Button>
        </div>
      )}

      {open && (
        <LineDrawer catalogId={entry.catalog_id} line={open.line} isNew={open.isNew} context={context} canEdit={editable}
                    original={open.isNew ? null : original.get(open.line.target_column.toUpperCase()) ?? null}
                    onClose={() => setOpen(null)} onStage={(l) => stage(l, open.isNew)} />
      )}
    </div>
  );
}
