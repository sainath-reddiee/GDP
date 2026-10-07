"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight, Bot, Check, CheckCheck, CircleSlash, KeyRound, ListChecks, Loader2, Search, Sparkles, Target, X,
} from "lucide-react";
import type { MappingOverview, MappingSuggestion } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { assistMapping, saveMappingDecisions } from "../pipeline-actions";
import { MappingDetail, pct, ScoreBar } from "./mapping-detail";

/** Target columns the platform fills (keys, record source, audit, hub reference); never mapped from a source. */
const SYSTEM_DERIVED: (string | null)[] = ["SURROGATE_KEY", "RECORD_SOURCE", "AUDIT_TIMESTAMP", "DERIVED_KEY"];
import {
  buildRows, planAcceptAi, planApproveTop, planNull, withJustification, type BulkPlan, type MappingRow,
} from "./mapping-plan";

type Filter = "all" | "pending" | "confident" | "weak" | "ai" | "disagree" | "mapped" | "null";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "pending", label: "Needs review" },
  { id: "confident", label: "Confident ≥80%" },
  { id: "weak", label: "Weak <45%" },
  { id: "ai", label: "AI suggested" },
  { id: "disagree", label: "AI disagrees" },
  { id: "mapped", label: "Mapped" },
  { id: "null", label: "NULL" },
];

type Pending = { title: string; plan: BulkPlan; defaultNote: string } | null;

export function MappingBoard({ runId, data }: { runId: string; data: MappingOverview }) {
  const router = useRouter();
  const rows = useMemo(() => buildRows(data), [data]);
  const [filter, setFilter] = useState<Filter>("pending");
  const [query, setQuery] = useState("");
  const [table, setTable] = useState("");
  const [sort, setSort] = useState<"table" | "score_desc" | "score_asc">("table");
  const [view, setView] = useState<"sources" | "targets">("sources");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string>(() => rows.find((r) => r.status === "pending")?.id ?? rows[0]?.id ?? "");
  const [suggestions, setSuggestions] = useState<Record<string, MappingSuggestion>>({});
  const [aiModel, setAiModel] = useState<string | null>(null);
  const [aiNote, setAiNote] = useState("");
  const [showAi, setShowAi] = useState(false);
  const [confirm, setConfirm] = useState<Pending>(null);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const [aiPending, startAi] = useTransition();

  const tables = useMemo(() => [...new Set(rows.map((r) => r.table))].sort(), [rows]);
  const takenBy = useMemo(() => {
    const m = new Map<string, string>();
    rows.forEach((r) => { if (r.mappedTargetId) m.set(r.mappedTargetId, r.column); });
    return m;
  }, [rows]);
  const counts = useMemo(() => ({
    mapped: rows.filter((r) => r.status === "mapped").length,
    null: rows.filter((r) => r.status === "null").length,
    pending: rows.filter((r) => r.status === "pending").length,
    confident: rows.filter((r) => r.status === "pending" && r.score >= 0.8).length,
  }), [rows]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (table && r.table !== table) return false;
      if (q && !`${r.column} ${r.top.target_column} ${r.mappedName ?? ""}`.toLowerCase().includes(q)) return false;
      switch (filter) {
        case "pending": return r.status === "pending";
        case "confident": return r.status === "pending" && r.score >= 0.8;
        case "weak": return r.score < 0.45;
        case "ai": return Boolean(suggestions[r.id]);
        case "disagree": return r.top.llm_agrees === false
          || (suggestions[r.id] && suggestions[r.id].target_column_id !== r.top.target_column_id);
        case "mapped": return r.status === "mapped";
        case "null": return r.status === "null";
        default: return true;
      }
    });
    if (sort === "score_desc") out.sort((a, b) => b.score - a.score);
    if (sort === "score_asc") out.sort((a, b) => a.score - b.score);
    return out;
  }, [rows, filter, query, table, sort, suggestions]);

  const focused = rows.find((r) => r.id === focus);
  const allVisibleSelected = visible.length > 0 && visible.every((r) => selected.has(r.id));

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const refresh = useCallback(() => router.refresh(), [router]);

  const commit = (decisions: Record<string, unknown>[], done: string, skipped: BulkPlan["skipped"] = []) =>
    start(async () => {
      setMessage(null);
      if (!decisions.length) {
        setMessage({ tone: "error", text: skipped.length ? `Nothing saved: ${skipped.map((s) => `${s.column} (${s.reason})`).join("; ")}` : "Nothing to save." });
        return;
      }
      const result = await saveMappingDecisions(runId, decisions);
      if (!result.ok) { setMessage({ tone: "error", text: result.error }); return; }
      const saved = new Set(decisions.map((d) => String(d.source_column_id)));
      setSelected(new Set());
      setSuggestions((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => !saved.has(id))));
      setConfirm(null);
      setMessage({
        tone: "ok",
        text: `${done}${skipped.length ? ` · skipped ${skipped.length}: ${skipped.slice(0, 4).map((s) => `${s.column} (${s.reason})`).join("; ")}${skipped.length > 4 ? "…" : ""}` : ""}`,
      });
      refresh();
    });

  const runPlan = (title: string, plan: BulkPlan, defaultNote: string) => {
    if (plan.needsReason || plan.skipped.length) {
      setNote(defaultNote);
      setConfirm({ title, plan, defaultNote });
      return;
    }
    commit(withJustification(plan, ""), `${title}: ${plan.decisions.length} saved`);
  };

  const approveSelected = (ids: Set<string>, minScore = 0, label = "Approved suggested targets") =>
    runPlan(label, planApproveTop(rows, ids, minScore), "Reviewed the ranked evidence; the top candidate is the correct target.");

  const nullSelected = (ids: Set<string>) =>
    commit(withJustification(planNull(rows, ids), "No matching target column; load this source field as NULL."),
      `Set ${[...ids].length} column(s) to NULL`);

  const askAi = (ids: string[]) => startAi(async () => {
    setMessage(null);
    const result = await assistMapping(runId, ids, aiNote);
    if (!result.ok) { setMessage({ tone: "error", text: result.error }); return; }
    setSuggestions((prev) => ({ ...prev, ...Object.fromEntries(result.data.suggestions.map((s) => [s.source_column_id, s])) }));
    setAiModel(result.data.model);
    setFilter("ai");
    setMessage({
      tone: "ok",
      text: `AI reviewed ${result.data.suggestions.length} column(s)${result.data.skipped ? `; ${result.data.skipped} more over the batch limit, run again for the rest` : ""}. Nothing is saved until you accept.`,
    });
  });

  const acceptAi = (ids?: Set<string>) => {
    const plan = planAcceptAi(rows, Object.values(suggestions), ids);
    commit(plan.decisions, `Accepted ${plan.decisions.length} AI suggestion(s)`, plan.skipped);
  };

  // keyboard: j/k move, x select, a approve, n null
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable || pending) return;
      const index = visible.findIndex((r) => r.id === focus);
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocus(visible[Math.min(visible.length - 1, index + 1)]?.id ?? focus);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocus(visible[Math.max(0, index - 1)]?.id ?? focus);
      } else if (e.key === "x" && focus) {
        toggle(focus);
      } else if (e.key === "a" && focus) {
        approveSelected(new Set([focus]), 0, "Approved");
      } else if (e.key === "n" && focus) {
        nullSelected(new Set([focus]));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    document.querySelector(`[data-row="${focus}"]`)?.scrollIntoView({ block: "nearest" });
  }, [focus]);

  const total = rows.length || 1;
  const required = data.targets.filter((t) => !t.nullable && !SYSTEM_DERIVED.includes(t.semantic_type));
  const aiCount = Object.keys(suggestions).length;

  return (
    <Card className="overflow-hidden">
      <CardHeader className="space-y-3 border-b pb-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              Source → {data.target_table?.target_table ?? "target"} mapping
            </CardTitle>
            <CardDescription>
              {data.status.decided}/{data.status.source_columns} columns decided · {data.targets.length} target columns
              {data.target_table ? ` in ${data.target_table.target_database}.${data.target_table.target_schema}` : ""}
            </CardDescription>
          </div>
          <div className="flex rounded-lg border p-0.5 text-xs">
            {(["sources", "targets"] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)}
                className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5", view === v ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
                {v === "sources" ? <ListChecks className="h-3.5 w-3.5" /> : <Target className="h-3.5 w-3.5" />}
                {v === "sources" ? "Source columns" : "Target coverage"}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
            <span className="bg-emerald-500" style={{ width: `${(counts.mapped / total) * 100}%` }} />
            <span className="bg-slate-400" style={{ width: `${(counts.null / total) * 100}%` }} />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />{counts.mapped} mapped</span>
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-400" />{counts.null} NULL</span>
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-muted-foreground/30" />{counts.pending} pending</span>
            <span>{required.length - data.status.missing_required_targets.length}/{required.length} required targets covered</span>
            {aiCount > 0 && <span className="text-violet-700"><Bot className="mr-0.5 inline h-3 w-3" />{aiCount} AI suggestions{aiModel ? ` · ${aiModel}` : ""}</span>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={pending || !counts.confident}
            onClick={() => approveSelected(new Set(rows.filter((r) => r.status === "pending" && r.score >= 0.8).map((r) => r.id)), 0.8, "Approved all confident matches")}>
            <CheckCheck className="h-4 w-4" /> Approve all confident ({counts.confident})
          </Button>
          <Button size="sm" variant="outline" className="border-violet-300 text-violet-700 hover:bg-violet-50" disabled={aiPending || !counts.pending}
            onClick={() => setShowAi((v) => !v)}>
            {aiPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />} AI copilot
          </Button>
          {aiCount > 0 && (
            <Button size="sm" variant="outline" disabled={pending} onClick={() => acceptAi()}>
              <Sparkles className="h-4 w-4" /> Accept all AI suggestions ({aiCount})
            </Button>
          )}
          <span className="ml-auto hidden text-[11px] text-muted-foreground md:inline">Keys: j/k move · x select · a approve · n NULL</span>
        </div>

        {showAi && (
          <div className="space-y-2 rounded-lg border border-violet-200 bg-violet-50/60 p-3 dark:bg-violet-950/20">
            <p className="text-sm font-medium">Ask the AI copilot to review columns</p>
            <p className="text-xs text-muted-foreground">
              It reads profile statistics, sample values, scored candidates and target definitions, then proposes approve, remap or NULL for each column.
              Proposals are not saved until you accept them.
            </p>
            <Textarea rows={2} value={aiNote} onChange={(e) => setAiNote(e.target.value)}
              placeholder="Optional guidance, e.g. 'GEOMETRY columns are not loaded', 'prefer *_LID columns as business keys'" />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={aiPending || !counts.pending} onClick={() => askAi([])}>
                {aiPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />} Review all pending ({counts.pending})
              </Button>
              <Button size="sm" variant="outline" disabled={aiPending || !selected.size} onClick={() => askAi([...selected])}>
                Review selected ({selected.size})
              </Button>
            </div>
          </div>
        )}

        {message && (
          <p role={message.tone === "error" ? "alert" : "status"}
            className={cn("rounded-md px-3 py-2 text-sm", message.tone === "error" ? "bg-destructive/10 text-destructive" : "bg-emerald-50 text-emerald-800")}>
            {message.text}
          </p>
        )}

        {confirm && (
          <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
            <p className="font-medium">{confirm.title}: {confirm.plan.decisions.length} column(s)</p>
            {confirm.plan.skipped.length > 0 && (
              <ul className="list-inside list-disc text-xs text-amber-800">
                {confirm.plan.skipped.slice(0, 8).map((s) => <li key={s.column}>{s.column} skipped: {s.reason}</li>)}
                {confirm.plan.skipped.length > 8 && <li>…and {confirm.plan.skipped.length - 8} more</li>}
              </ul>
            )}
            {confirm.plan.needsReason && (
              <>
                <p className="text-xs text-muted-foreground">Some of these were not auto-suggested, so one business justification is recorded for the batch.</p>
                <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
              </>
            )}
            <div className="flex gap-2">
              <Button size="sm" disabled={pending || !confirm.plan.decisions.length || (confirm.plan.needsReason && !note.trim())}
                onClick={() => commit(withJustification(confirm.plan, note), `${confirm.title}: ${confirm.plan.decisions.length} saved`, confirm.plan.skipped)}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Confirm
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
            </div>
          </div>
        )}
      </CardHeader>

      <CardContent className="p-0">
        {view === "targets" ? (
          <TargetCoverage data={data} rows={rows} takenBy={takenBy} suggestions={suggestions}
            onOpen={(id) => { setView("sources"); setFilter("all"); setFocus(id); }} />
        ) : (
          <div className={cn("grid", focused ? "lg:grid-cols-[minmax(0,1fr)_400px]" : "")}>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 border-b p-3">
                <div className="relative min-w-[200px] flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search source or target columns" className="h-9 pl-8" />
                </div>
                {tables.length > 1 && (
                  <Select value={table} onChange={(e) => setTable(e.target.value)} className="w-44">
                    <option value="">All source tables</option>
                    {tables.map((t) => <option key={t} value={t}>{t}</option>)}
                  </Select>
                )}
                <Select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className="w-40">
                  <option value="table">Sort: table, name</option>
                  <option value="score_desc">Sort: best match</option>
                  <option value="score_asc">Sort: weakest first</option>
                </Select>
              </div>
              <div className="flex flex-wrap gap-1.5 border-b px-3 py-2">
                {FILTERS.map((f) => (
                  <button key={f.id} type="button" onClick={() => setFilter(f.id)}
                    className={cn("rounded-full border px-2.5 py-0.5 text-xs",
                      filter === f.id ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
                    {f.label}
                  </button>
                ))}
              </div>

              {selected.size > 0 && (
                <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b bg-primary/5 px-3 py-2 text-sm backdrop-blur">
                  <span className="font-medium">{selected.size} selected</span>
                  <Button size="sm" disabled={pending} onClick={() => approveSelected(selected)}>
                    <Check className="h-3.5 w-3.5" /> Approve suggested
                  </Button>
                  <Button size="sm" variant="outline" disabled={pending} onClick={() => nullSelected(selected)}>
                    <CircleSlash className="h-3.5 w-3.5" /> Set NULL
                  </Button>
                  <Button size="sm" variant="outline" className="text-violet-700" disabled={aiPending} onClick={() => askAi([...selected])}>
                    <Bot className="h-3.5 w-3.5" /> Ask AI
                  </Button>
                  {[...selected].some((id) => suggestions[id]) && (
                    <Button size="sm" variant="outline" disabled={pending} onClick={() => acceptAi(selected)}>
                      <Sparkles className="h-3.5 w-3.5" /> Accept AI for selected
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}><X className="h-3.5 w-3.5" /> Clear</Button>
                </div>
              )}

              <div className="max-h-[38rem] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-[5] bg-card text-left text-[11px] uppercase tracking-wide text-muted-foreground shadow-[0_1px_0_hsl(var(--border))]">
                    <tr>
                      <th className="w-8 px-3 py-2">
                        <input type="checkbox" aria-label="Select all visible" checked={allVisibleSelected}
                          onChange={() => setSelected((prev) => {
                            const next = new Set(prev);
                            visible.forEach((r) => (allVisibleSelected ? next.delete(r.id) : next.add(r.id)));
                            return next;
                          })} />
                      </th>
                      <th className="px-2 py-2">Source column</th>
                      <th className="px-2 py-2">Target</th>
                      <th className="px-2 py-2">Match</th>
                      <th className="px-2 py-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.length === 0 && (
                      <tr><td colSpan={5} className="px-3 py-10 text-center text-sm text-muted-foreground">
                        {filter === "pending" && counts.pending === 0 ? "Every column has a decision. Approve the mapping pack above." : "No columns match these filters."}
                      </td></tr>
                    )}
                    {visible.map((r) => {
                      const ai = suggestions[r.id];
                      const profile = data.profile?.[r.id];
                      const showTarget = r.status === "mapped" ? r.mappedName : r.status === "null" ? null : r.top.target_column;
                      const conflict = r.status === "pending" && takenBy.get(r.top.target_column_id);
                      return (
                        <tr key={r.id} data-row={r.id} onClick={() => setFocus(r.id)}
                          className={cn("cursor-pointer border-b last:border-0",
                            r.id === focus ? "bg-primary/5" : "hover:bg-muted/40",
                            selected.has(r.id) && "bg-sky-50/60")}>
                          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" aria-label={`Select ${r.column}`} checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                          </td>
                          <td className="max-w-[260px] px-2 py-2">
                            <span className="block truncate font-medium">{r.column}</span>
                            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                              <span className="truncate">{tables.length > 1 ? `${r.table} · ` : ""}{r.datatype}</span>
                              {profile?.null_percentage != null && Number(profile.null_percentage) >= 50 && <span className="text-amber-700">null {Number(profile.null_percentage).toFixed(0)}%</span>}
                              {profile?.pii && profile.pii !== "NONE" && <span className="text-rose-600">PII</span>}
                            </span>
                          </td>
                          <td className="max-w-[260px] px-2 py-2">
                            {r.status === "null" ? (
                              <span className="text-xs text-muted-foreground">loads as NULL</span>
                            ) : (
                              <span className="flex items-center gap-1.5">
                                <ArrowRight className={cn("h-3.5 w-3.5 shrink-0", r.status === "mapped" ? "text-emerald-600" : "text-muted-foreground")} />
                                <span className={cn("truncate", r.status === "mapped" && "font-medium")}>{showTarget}</span>
                                {r.top.is_business_key && r.status === "pending" && <KeyRound className="h-3 w-3 shrink-0 text-amber-500" />}
                              </span>
                            )}
                            {ai && r.status === "pending" && (
                              <span className="mt-0.5 flex items-center gap-1 text-[11px] text-violet-700" title={ai.reason}>
                                <Bot className="h-3 w-3" />
                                {ai.action === "NULL" ? "AI: leave NULL" : ai.target_column_id === r.top.target_column_id ? "AI agrees" : `AI: ${ai.target_column}`}
                                <span className="text-muted-foreground">· {pct(ai.confidence)}</span>
                              </span>
                            )}
                            {!ai && r.top.llm_agrees === false && r.status === "pending" && (
                              <span className="mt-0.5 block text-[11px] text-amber-700">AI preferred {r.top.llm_preferred}</span>
                            )}
                            {conflict && <span className="mt-0.5 block text-[11px] text-amber-700">target taken by {conflict}</span>}
                          </td>
                          <td className="whitespace-nowrap px-2 py-2">
                            <span className="flex items-center gap-2">
                              <ScoreBar value={r.score} />
                              <span className="w-8 text-xs tabular-nums">{pct(r.score)}</span>
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-2 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                            {r.status === "pending" ? (
                              <span className="inline-flex gap-1">
                                {ai && (
                                  <Button size="sm" variant="outline" className="h-7 px-2 text-violet-700" title="Accept AI suggestion" disabled={pending}
                                    onClick={() => acceptAi(new Set([r.id]))}>
                                    <Sparkles className="h-3.5 w-3.5" />
                                  </Button>
                                )}
                                <Button size="sm" variant="outline" className="h-7 px-2" title={`Approve ${r.top.target_column}`} disabled={pending || Boolean(conflict)}
                                  onClick={() => approveSelected(new Set([r.id]), 0, "Approved")}>
                                  <Check className="h-3.5 w-3.5" />
                                </Button>
                                <Button size="sm" variant="ghost" className="h-7 px-2" title="Leave unmapped (NULL)" disabled={pending}
                                  onClick={() => nullSelected(new Set([r.id]))}>
                                  <CircleSlash className="h-3.5 w-3.5" />
                                </Button>
                              </span>
                            ) : (
                              <Badge variant={r.status === "null" ? "secondary" : "success"} className="text-[10px]">
                                {r.status === "null" ? "NULL" : r.decision?.decision.replace("_", " ").toLowerCase()}
                              </Badge>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {focused && (
              <aside className="border-t lg:max-h-[48rem] lg:border-l lg:border-t-0">
                <MappingDetail
                  runId={runId} row={focused} data={data} profile={data.profile?.[focused.id]}
                  suggestion={suggestions[focused.id]} takenBy={takenBy}
                  onSaved={() => {
                    const next = visible.find((r) => r.id !== focused.id && r.status === "pending");
                    setSuggestions((prev) => { const { [focused.id]: _drop, ...rest } = prev; return rest; });
                    if (next) setFocus(next.id);
                    refresh();
                  }}
                  onClose={() => setFocus("")}
                />
              </aside>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TargetCoverage({
  data, rows, takenBy, suggestions, onOpen,
}: {
  data: MappingOverview;
  rows: MappingRow[];
  takenBy: Map<string, string>;
  suggestions: Record<string, MappingSuggestion>;
  onOpen: (sourceId: string) => void;
}) {
  const byColumn = new Map(rows.map((r) => [r.column, r]));
  const candidatesFor = (targetId: string) =>
    rows.filter((r) => r.status === "pending" && r.ranked.some((c) => c.target_column_id === targetId))
      .map((r) => ({ row: r, score: Number(r.ranked.find((c) => c.target_column_id === targetId)?.final_score ?? 0) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 3);
  const aiFor = (targetId: string) => Object.values(suggestions).find((s) => s.target_column_id === targetId);
  const systemDerived = SYSTEM_DERIVED;

  return (
    <div className="max-h-[44rem] overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-card text-left text-[11px] uppercase tracking-wide text-muted-foreground shadow-[0_1px_0_hsl(var(--border))]">
          <tr><th className="px-3 py-2">Target column</th><th className="px-2 py-2">Mapped from</th><th className="px-2 py-2">Best open candidates</th></tr>
        </thead>
        <tbody>
          {data.targets.map((t) => {
            const source = takenBy.get(t.target_column_id);
            const derived = systemDerived.includes(t.semantic_type);
            const required = !t.nullable && !derived;
            const ai = aiFor(t.target_column_id);
            return (
              <tr key={t.target_column_id} className={cn("border-b last:border-0", required && !source && "bg-rose-50/60")}>
                <td className="px-3 py-2">
                  <span className="flex items-center gap-1.5 font-medium">
                    {t.column_name}
                    {t.is_business_key && <KeyRound className="h-3 w-3 text-amber-500" />}
                    {required && <Badge variant={source ? "outline" : "destructive"} className="text-[10px]">required</Badge>}
                    {derived && <Badge variant="secondary" className="text-[10px]">system</Badge>}
                  </span>
                  <span className="text-[11px] text-muted-foreground">{t.data_type}{t.definition ? ` · ${t.definition}` : ""}</span>
                </td>
                <td className="px-2 py-2">
                  {source ? (
                    <button type="button" className="font-medium text-emerald-700 hover:underline" onClick={() => onOpen(byColumn.get(source)!.id)}>
                      ← {source}
                    </button>
                  ) : derived ? (
                    <span className="text-xs text-muted-foreground">generated by the model</span>
                  ) : (
                    <span className="text-xs text-muted-foreground">unmapped{t.nullable ? " (loads NULL)" : ""}</span>
                  )}
                </td>
                <td className="px-2 py-2">
                  {!source && !derived && (
                    <span className="flex flex-wrap gap-1">
                      {ai && (
                        <button type="button" onClick={() => onOpen(ai.source_column_id)}
                          className="rounded-full border border-violet-300 px-2 py-0.5 text-[11px] text-violet-700 hover:bg-violet-50">
                          <Bot className="mr-0.5 inline h-3 w-3" />{rows.find((r) => r.id === ai.source_column_id)?.column}
                        </button>
                      )}
                      {candidatesFor(t.target_column_id).map(({ row, score }) => (
                        <button key={row.id} type="button" onClick={() => onOpen(row.id)}
                          className="rounded-full border px-2 py-0.5 text-[11px] hover:bg-muted">
                          {row.column} · {pct(score)}
                        </button>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
