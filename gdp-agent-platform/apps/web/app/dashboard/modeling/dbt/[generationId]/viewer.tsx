"use client";

import { useMemo, useState, useTransition } from "react";
import { FileCode2, Folder, GitCompareArrows, Info, Loader2, Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DbtGenerationDetail } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SqlEditor } from "@/components/quality/sql-editor";
import { explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { dbtDiff, reviewDbtFile } from "../../actions";

type Review = Awaited<ReturnType<typeof reviewDbtFile>>;
type Diff = Awaited<ReturnType<typeof dbtDiff>>;
const RELEASE = /^release\//;

function Patch({ text }: { text: string }) {
  return (
    <pre className="max-h-[520px] overflow-auto rounded-lg border bg-muted/40 p-2 font-mono text-[11px] leading-5">
      {text.split("\n").map((line, i) => (
        <div key={i} className={cn(line.startsWith("+") && !line.startsWith("+++") ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : line.startsWith("-") && !line.startsWith("---") ? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
          : line.startsWith("@@") ? "text-sky-700 dark:text-sky-300" : "")}>{line || " "}</div>
      ))}
    </pre>
  );
}

/** A run's generated dbt project, read-only: dbt is regenerated from the approved model and the STTM, so changes are
 *  made there. Shows each file, the changes since the run's previous generation, and an AI review (advice only). */
export function DbtViewer({ detail, initialFile, canReview }: { detail: DbtGenerationDetail; initialFile: string | null; canReview: boolean }) {
  const toast = useToast();
  const files = useMemo(() => detail.files.filter((f) => !RELEASE.test(f.file_path)), [detail.files]);
  const [path, setPath] = useState(initialFile && files.some((f) => f.file_path === initialFile) ? initialFile : (files.find((f) => f.file_path.startsWith("models/"))?.file_path ?? files[0]?.file_path ?? ""));
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"code" | "changes">("code");
  const [review, setReview] = useState<Review | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [pending, start] = useTransition();
  const file = files.find((f) => f.file_path === path);
  const changed = diff && diff.ok ? new Map(diff.data.files.map((f) => [f.file_path, f])) : null;
  const groups = useMemo(() => {
    const out = new Map<string, typeof files>();
    for (const f of files.filter((f) => !q || f.file_path.toLowerCase().includes(q.toLowerCase()))) {
      const dir = f.file_path.includes("/") ? f.file_path.slice(0, f.file_path.lastIndexOf("/")) : ".";
      out.set(dir, [...(out.get(dir) ?? []), f]);
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [files, q]);

  const showChanges = () => start(async () => {
    setMode("changes");
    if (!diff) {
      const r = await dbtDiff(detail.generation.generation_id);
      setDiff(r);
      if (!r.ok) toast("error", explain(r.error).message);
    }
  });
  const runReview = () => start(async () => {
    setReview(null);
    const r = await reviewDbtFile(detail.generation.run_id, path);
    if (r.ok) setReview(r);
    else toast("error", explain(r.error).message);
  });

  if (!files.length) return <p className="text-sm text-muted-foreground">This generation has no files.</p>;
  const filePatch = changed?.get(path);
  return (
    <div className="space-y-4">
      <p className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100" data-testid="dbt-readonly">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <span>dbt is generated from the approved domain model and the Data Mapping, so it is read-only here. Change the model in the Model Workbench or the mapping in the Data Mapping Store, then regenerate dbt on the run.</span>
      </p>
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="surface space-y-2 p-3" aria-label="Files">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a file" className="h-8 pl-8 text-xs" aria-label="Find a file" />
          </div>
          <div className="max-h-[560px] space-y-2 overflow-auto">
            {groups.map(([dir, fs]) => (
              <div key={dir}>
                <p className="flex items-center gap-1 px-1 text-[11px] font-medium text-muted-foreground"><Folder className="h-3 w-3" /> {dir}</p>
                {fs.map((f) => {
                  const c = changed?.get(f.file_path);
                  return (
                    <button key={f.file_path} type="button" onClick={() => { setPath(f.file_path); setReview(null); }} data-testid={`file-${f.file_path}`}
                            className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left font-mono text-[11px] transition",
                              f.file_path === path ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-muted")}>
                      <FileCode2 className="h-3 w-3 shrink-0" /><span className="truncate">{f.file_path.split("/").pop()}</span>
                      {c && <span className={cn("ml-auto rounded px-1 text-[9px] font-semibold uppercase", c.status === "added" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-800")}>{c.status}</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </aside>
        <section className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-sm font-medium">{path}</p>
            <span className="text-[11px] text-muted-foreground">{file?.artifact_type.toLowerCase().replace(/_/g, " ")}</span>
            <span className="ml-auto flex gap-2">
              <div className="flex rounded-xl bg-muted p-1 text-xs">
                <button type="button" onClick={() => setMode("code")} className={cn("rounded-lg px-2.5 py-1 font-medium", mode === "code" ? "bg-card shadow-xs" : "text-muted-foreground")}>Code</button>
                <button type="button" onClick={showChanges} data-testid="dbt-changes" className={cn("flex items-center gap-1 rounded-lg px-2.5 py-1 font-medium", mode === "changes" ? "bg-card shadow-xs" : "text-muted-foreground")}>
                  <GitCompareArrows className="h-3 w-3" /> Changes</button>
              </div>
              {canReview && <Button size="sm" variant="outline" onClick={runReview} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Review with AI</Button>}
            </span>
          </div>
          {mode === "code" && <SqlEditor value={file?.content ?? ""} readOnly rows={Math.min(34, Math.max(12, (file?.content ?? "").split("\n").length + 1))} label="File content" />}
          {mode === "changes" && (pending && !diff ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Comparing with the previous generation...</p>
            : diff && diff.ok ? (
              <div className="space-y-2" data-testid="dbt-diff">
                <p className="text-xs text-muted-foreground">{diff.data.base ? `Compared with generation v${diff.data.base.generation_version}: ${diff.data.files.length} file${diff.data.files.length === 1 ? "" : "s"} changed.` : "This is the run's first generation."}</p>
                {filePatch ? <Patch text={filePatch.patch} /> : <p className="text-sm text-muted-foreground">{path} did not change.</p>}
              </div>
            ) : null)}
          {review && review.ok && (
            <div className="surface space-y-2 p-3" data-testid="dbt-review">
              <p className="flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="h-4 w-4 text-blue-600" /> AI review (advice)</p>
              <p className="text-sm">{review.data.summary}</p>
              <ul className="space-y-1 text-xs">
                {review.data.findings.map((f, i) => (
                  <li key={i}><span className={cn("mr-1 rounded px-1 py-0.5 text-[10px] font-semibold uppercase", f.severity === "error" ? "bg-rose-100 text-rose-700" : f.severity === "warning" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700")}>{f.severity}</span>
                    {f.message}{f.line_hint ? <span className="text-muted-foreground"> ({f.line_hint})</span> : null}</li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">To act on a finding, change the model or the Data Mapping and regenerate dbt.</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
