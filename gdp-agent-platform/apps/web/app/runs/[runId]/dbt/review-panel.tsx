"use client";

import { useMemo, useState, useTransition } from "react";
import {
  Bot, ChevronRight, FileCode2, FileText, Folder, GitCompare, Loader2, Search, ShieldCheck, Sparkles, Wand2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { applyDbtEnhance, previewDbtEnhance, reviewDbtFile } from "../pipeline-actions";
import type { CortexModel, DbtArtifact, GenerationReport, ReviewResult } from "./dbt-types";
import { Callout, CodeView, DiffView, Empty, Stat, StatusPill, type Tone, useToast } from "./studio-ui";
import { CodeCitations } from "@/components/code-citations";

const CLASS_TONE: Record<string, Tone> = {
  PASSTHROUGH: "done", DERIVED: "active", AUDIT: "idle", COMPOUND_PK: "active", HKEY: "idle", SEQUENCE: "idle",
  SOURCE_SYSTEM_REF: "idle", FK_LOOKUP: "warn", HUB_FK: "warn", STANDARDIZATION: "warn", LOV: "warn", UNMAPPED: "idle",
};
const INTERNAL = /^release\/(branch|skills|workspace|skeleton-base)\.json$/;

type Tab = "code" | "diff" | "review" | "enhance";

function fileIcon(path: string) {
  return path.endsWith(".sql") ? FileCode2 : FileText;
}

export function ReviewPanel({
  runId, artifacts, report, skeletonBase, models, defaultModel, canEdit,
}: {
  runId: string;
  artifacts: DbtArtifact[];
  report: GenerationReport | null;
  skeletonBase: Record<string, string> | null;
  models: CortexModel[];
  defaultModel: string;
  canEdit: boolean;
}) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [showInternal, setShowInternal] = useState(false);
  const files = useMemo(
    () => artifacts.filter((a) => showInternal || !INTERNAL.test(a.file_path)),
    [artifacts, showInternal],
  );
  const firstModel = files.find((a) => a.file_path.startsWith("models/silver/") && a.file_path.endsWith(".sql"));
  const [open, setOpen] = useState(firstModel?.file_path ?? files[0]?.file_path ?? "");
  const [tab, setTab] = useState<Tab>("code");
  const [model, setModel] = useState(defaultModel);
  const [reviews, setReviews] = useState<Record<string, ReviewResult>>({});
  const [prompt, setPrompt] = useState("");
  const [preview, setPreview] = useState<{ path: string; content: string; summary?: string } | null>(null);
  const [busy, setBusy] = useState<"review" | "enhance" | "apply" | null>(null);
  const [, start] = useTransition();
  const [showColumns, setShowColumns] = useState(false);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? files.filter((a) => a.file_path.toLowerCase().includes(q)) : files;
  }, [files, query]);
  const tree = useMemo(() => {
    const groups = new Map<string, DbtArtifact[]>();
    for (const a of visible) {
      const dir = a.file_path.includes("/") ? a.file_path.slice(0, a.file_path.lastIndexOf("/")) : "";
      groups.set(dir, [...(groups.get(dir) ?? []), a]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [visible]);
  const current = artifacts.find((a) => a.file_path === open) ?? visible[0];
  const status = (path: string) => report?.files?.[path];
  const base = current ? skeletonBase?.[current.file_path] : undefined;
  const review = current ? reviews[current.file_path] : undefined;
  const modelOptions = models.length ? models : [{ name: model } as CortexModel];

  const runReview = () => {
    if (!current) return;
    setBusy("review");
    start(async () => {
      const result = await reviewDbtFile(runId, current.file_path, model);
      setBusy(null);
      if (!result.ok) { toast("fail", result.error); return; }
      setReviews((r) => ({ ...r, [current.file_path]: result.data }));
      setTab("review");
      toast(result.data.findings.length ? "warn" : "done",
        result.data.findings.length ? `${result.data.findings.length} finding(s) in ${current.file_path}` : "No skill violations found.");
    });
  };

  const runEnhance = () => {
    if (!current || !prompt.trim()) return;
    setBusy("enhance");
    start(async () => {
      const result = await previewDbtEnhance(runId, { file_path: current.file_path, prompt: prompt.trim(), model });
      setBusy(null);
      if (!result.ok) { toast("fail", result.error); return; }
      setPreview({ path: current.file_path, content: result.data.content, summary: result.data.summary || result.data.rationale });
    });
  };

  const accept = (path: string, content: string) => {
    setBusy("apply");
    start(async () => {
      const result = await applyDbtEnhance(runId, { file_path: path, content });
      setBusy(null);
      if (!result.ok) { toast("fail", result.error); return; }
      setPreview(null);
      setReviews((r) => { const next = { ...r }; delete next[path]; return next; });
      setPrompt("");
      toast("done", `Applied to ${path}. Push again to update the PR.`);
    });
  };

  if (!artifacts.length) {
    return <Empty icon={FileCode2} title="No generated code yet">Generate the project to review models, YAML and macros here.</Empty>;
  }

  const counts = report?.counts ?? {};
  const columns = report?.columns ?? [];
  const todos = report?.todos ?? 0;
  const anomalies = report?.anomalies ?? [];

  return (
    <div className="space-y-4">
      {report && (
        <div className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Target columns" value={columns.length} hint={report.target} />
            <Stat label="Mapped" value={(counts.PASSTHROUGH ?? 0) + (counts.DERIVED ?? 0)} hint="passthrough + derived" tone="done" />
            <Stat label="Casts" value={report.casts ?? 0} hint="only on type mismatch" />
            <Stat label="TODOs" value={todos} hint="FK / LOV / standardization" tone={todos ? "warn" : "done"} />
            <Stat label="Source key" value={report.source_unique_id?.columns?.join(" + ") || "—"} hint={report.source_unique_id?.reason} />
            <Stat label="Skeleton" value={report.skeleton_files ?? 0} hint={report.hub} />
          </div>
          {anomalies.length > 0 && (
            <Callout tone="warn" title={`${anomalies.length} thing(s) to check before merging`}>
              <ul className="list-inside list-disc space-y-0.5">{anomalies.map((a) => <li key={a}>{a}</li>)}</ul>
            </Callout>
          )}
          <div className="rounded-lg border">
            <button type="button" onClick={() => setShowColumns((s) => !s)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium">
              <span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-emerald-600" /> How each column was built (skill rulebook)</span>
              <ChevronRight className={cn("h-4 w-4 transition-transform", showColumns && "rotate-90")} />
            </button>
            {showColumns && (
              <div className="max-h-80 overflow-auto border-t">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-left text-[11px] uppercase text-muted-foreground">
                    <tr><th className="px-3 py-1.5">Target</th><th className="px-3 py-1.5">Rule class</th><th className="px-3 py-1.5">Source</th>
                      <th className="px-3 py-1.5">Cast</th><th className="px-3 py-1.5">Note</th></tr>
                  </thead>
                  <tbody>
                    {columns.map((c) => (
                      <tr key={c.target_column} className="border-t">
                        <td className="px-3 py-1.5 font-mono">{c.target_column}</td>
                        <td className="px-3 py-1.5"><StatusPill tone={CLASS_TONE[c.class] ?? "idle"}>{c.class}</StatusPill></td>
                        <td className="px-3 py-1.5 font-mono text-muted-foreground">{c.source ?? "—"}</td>
                        <td className="px-3 py-1.5">{c.cast ?? ""}</td>
                        <td className="px-3 py-1.5 text-muted-foreground">{c.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="grid min-h-[34rem] overflow-hidden rounded-xl border lg:grid-cols-[280px_1fr]">
        <aside className="flex flex-col border-b bg-slate-50/60 lg:border-b-0 lg:border-r">
          <div className="space-y-2 border-b p-2">
            <div className="relative">
              <Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input aria-label="Search files" className="h-8 pl-7 text-xs" placeholder="Search files" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <label className="flex items-center gap-2 px-1 text-[11px] text-muted-foreground">
              <input type="checkbox" checked={showInternal} onChange={(e) => setShowInternal(e.target.checked)} /> Show pipeline metadata
            </label>
          </div>
          <nav className="flex-1 overflow-auto p-1.5 text-xs">
            {tree.map(([dir, items]) => (
              <div key={dir} className="mb-1">
                {dir && <p className="flex items-center gap-1 px-2 py-1 font-medium text-slate-500"><Folder className="h-3.5 w-3.5" />{dir}</p>}
                {items.map((a) => {
                  const Icon = fileIcon(a.file_path);
                  const s = status(a.file_path);
                  return (
                    <button key={a.artifact_id} type="button" onClick={() => { setOpen(a.file_path); setTab("code"); setPreview(null); }}
                      className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left",
                        dir && "pl-5", a.file_path === current?.file_path ? "bg-white font-medium shadow-sm ring-1 ring-slate-200" : "hover:bg-white/70")}>
                      <Icon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                      <span className="min-w-0 flex-1 truncate font-mono">{a.file_path.split("/").pop()}</span>
                      {s === "patched" && <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-800">M</span>}
                      {s === "new" && <span className="rounded bg-emerald-100 px-1 text-[10px] text-emerald-800">A</span>}
                      {reviews[a.file_path]?.findings.length ? <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> : null}
                    </button>
                  );
                })}
              </div>
            ))}
            {!visible.length && <p className="p-3 text-muted-foreground">No files match.</p>}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-col">
          {current && (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
                <span className="truncate font-mono text-sm">{current.file_path}</span>
                {status(current.file_path) && (
                  <StatusPill tone={status(current.file_path) === "patched" ? "warn" : "done"}>
                    {status(current.file_path) === "patched" ? "patched on skeleton" : "new"}
                  </StatusPill>
                )}
                <div className="ml-auto flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
                  {([["code", "Code"], ["diff", "Diff"], ["review", "AI review"], ["enhance", "Ask Cortex"]] as [Tab, string][])
                    .filter(([t]) => t !== "diff" || base !== undefined)
                    .map(([t, label]) => (
                      <button key={t} type="button" onClick={() => setTab(t)}
                        className={cn("rounded-md px-2.5 py-1", tab === t ? "bg-white font-medium shadow-sm" : "text-slate-600")}>
                        {label}{t === "review" && review ? ` (${review.findings.length})` : ""}
                      </button>
                    ))}
                </div>
              </div>
              <div className="flex-1 space-y-3 overflow-auto p-3">
                {tab === "code" && (preview?.path === current.file_path
                  ? <DiffView before={current.content} after={preview.content} />
                  : <CodeView text={current.content} />)}
                {tab === "diff" && base !== undefined && <DiffView before={base} after={current.content} />}
                {tab === "review" && (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Select aria-label="Cortex model" className="h-8 w-56 text-xs" value={model} onChange={(e) => setModel(e.target.value)}>
                        {modelOptions.map((m) => <option key={m.name} value={m.name}>{m.family ? `${m.family} · ` : ""}{m.name}</option>)}
                      </Select>
                      <Button size="sm" onClick={runReview} disabled={busy !== null}>
                        {busy === "review" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />}
                        {review ? "Review again" : "Review against DBT-ONBOARD-SOURCE"}
                      </Button>
                      <span className="text-[11px] text-muted-foreground">Cortex checks this file against the skill rules and the approved STTM.</span>
                    </div>
                    {!review && busy !== "review" && (
                      <Empty icon={ShieldCheck} title="Not reviewed yet">
                        The rules engine already applies the skill. An AI review is a second opinion that cites each rule.
                      </Empty>
                    )}
                    {review && (
                      <>
                        <Callout tone={review.findings.some((f) => f.severity === "error") ? "fail" : review.findings.length ? "warn" : "done"}
                          title={review.summary || (review.findings.length ? `${review.findings.length} finding(s)` : "Looks good")}>
                          Reviewed by {review.model}.
                        </Callout>
                        <CodeCitations items={review.code_citations} label="Compared with client code" />
                        <ul className="space-y-2">
                          {review.findings.map((f, i) => (
                            <li key={i} className="rounded-lg border p-2.5 text-sm">
                              <div className="flex items-center gap-2">
                                <StatusPill tone={f.severity === "error" ? "fail" : f.severity === "warning" ? "warn" : "idle"}>{f.severity}</StatusPill>
                                <span className="text-xs font-medium text-slate-600">{f.rule}</span>
                              </div>
                              <p className="mt-1">{f.message}</p>
                              {f.line_hint && <code className="mt-1 block truncate rounded bg-slate-50 px-2 py-1 font-mono text-[11px]">{f.line_hint}</code>}
                            </li>
                          ))}
                        </ul>
                        {review.rejected_revision.length > 0 && (
                          <Callout tone="warn" title="Cortex proposed a rewrite that was blocked">
                            {review.rejected_revision.join("; ")}
                          </Callout>
                        )}
                        {review.revised_content && (
                          <div className="space-y-2">
                            <p className="text-sm font-medium">Proposed fix</p>
                            <DiffView before={current.content} after={review.revised_content} />
                            {canEdit && (
                              <Button size="sm" onClick={() => accept(current.file_path, review.revised_content)} disabled={busy !== null}>
                                {busy === "apply" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} Accept fix
                              </Button>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
                {tab === "enhance" && (
                  <div className="space-y-3">
                    <p className="text-xs text-muted-foreground">
                      Describe a change. Cortex gets the skill rules and the STTM, rewrites the file, and you review the diff before applying it.
                    </p>
                    <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                      placeholder="e.g. resolve COUNTY_SKEY with a ref CTE on REF_COUNTY, add a not_null test on zip" />
                    <div className="flex flex-wrap gap-1.5">
                      {["Resolve the TODO columns", "Add tests for keys and accepted values", "Document every column", "Tighten casts to target types"].map((h) => (
                        <button key={h} type="button" onClick={() => setPrompt(h)} className="rounded-full border px-2.5 py-1 text-[11px] hover:bg-slate-50">{h}</button>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Select aria-label="Cortex model" className="h-8 w-56 text-xs" value={model} onChange={(e) => setModel(e.target.value)}>
                        {modelOptions.map((m) => <option key={m.name} value={m.name}>{m.family ? `${m.family} · ` : ""}{m.name}</option>)}
                      </Select>
                      <Button size="sm" variant="outline" onClick={runEnhance} disabled={busy !== null || !prompt.trim()}>
                        {busy === "enhance" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Preview change
                      </Button>
                    </div>
                    {preview?.path === current.file_path && (
                      <div className="space-y-2">
                        {preview.summary && <p className="text-sm text-muted-foreground">{preview.summary}</p>}
                        <DiffView before={current.content} after={preview.content} />
                        <div className="flex gap-2">
                          {canEdit && (
                            <Button size="sm" onClick={() => accept(current.file_path, preview.content)} disabled={busy !== null}>
                              {busy === "apply" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompare className="h-4 w-4" />} Apply to file
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Discard</Button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
