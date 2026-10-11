"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { FileCode2, Folder, Loader2, RotateCcw, Search, Send, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DbtGenerationDetail } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SqlEditor } from "@/components/quality/sql-editor";
import { explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { reviewDbtFile, saveDbtFile } from "../../actions";

type Review = Awaited<ReturnType<typeof reviewDbtFile>>;
const RELEASE = /^release\//;

export function DbtViewer({ detail, initialFile, canEdit, canReview }: {
  detail: DbtGenerationDetail; initialFile: string | null; canEdit: boolean; canReview: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const files = useMemo(() => detail.files.filter((f) => !RELEASE.test(f.file_path)), [detail.files]);
  const [path, setPath] = useState(initialFile && files.some((f) => f.file_path === initialFile) ? initialFile : (files.find((f) => f.file_path.startsWith("models/"))?.file_path ?? files[0]?.file_path ?? ""));
  const [q, setQ] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [pending, start] = useTransition();
  const file = files.find((f) => f.file_path === path);
  const value = drafts[path] ?? file?.content ?? "";
  const changed = Object.entries(drafts).filter(([p, c]) => c !== files.find((f) => f.file_path === p)?.content);
  const groups = useMemo(() => {
    const out = new Map<string, typeof files>();
    for (const f of files.filter((f) => !q || f.file_path.toLowerCase().includes(q.toLowerCase()))) {
      const dir = f.file_path.includes("/") ? f.file_path.slice(0, f.file_path.lastIndexOf("/")) : ".";
      out.set(dir, [...(out.get(dir) ?? []), f]);
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [files, q]);

  const submit = () => start(async () => {
    let queued = 0;
    for (const [p, content] of changed) {
      const base = files.find((f) => f.file_path === p)!;
      const r = await saveDbtFile(detail.generation.generation_id, { file_path: p, content, base_sha: base.content_sha256, note: note.trim() });
      if (r.ok) toast("ok", r.data.new_version ? `Saved as generation v${r.data.generation_version}` : `${p} saved`);
      else if (r.pending) queued += 1;
      else { toast("error", `${p}: ${explain(r.error).message}`); return; }
    }
    if (queued) toast("info", `Sent to CODE_REVIEWER for approval (${queued} file${queued === 1 ? "" : "s"}).`);
    setDrafts({}); setNote(""); router.refresh();
  });
  const runReview = () => start(async () => {
    setReview(null);
    const r = await reviewDbtFile(detail.generation.run_id, path);
    if (r.ok) setReview(r);
    else toast("error", explain(r.error).message);
  });

  if (!files.length) return <p className="text-sm text-muted-foreground">This generation has no files.</p>;
  return (
    <div className="space-y-4">
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
                  const dirty = drafts[f.file_path] !== undefined && drafts[f.file_path] !== f.content;
                  return (
                    <button key={f.file_path} type="button" onClick={() => { setPath(f.file_path); setReview(null); }} data-testid={`file-${f.file_path}`}
                            className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left font-mono text-[11px] transition",
                              f.file_path === path ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-muted")}>
                      <FileCode2 className="h-3 w-3 shrink-0" /><span className="truncate">{f.file_path.split("/").pop()}</span>
                      {dirty && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-warning" aria-label="changed" />}
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
              {drafts[path] !== undefined && <Button size="sm" variant="ghost" onClick={() => setDrafts(({ [path]: _, ...rest }) => rest)}><RotateCcw className="h-3.5 w-3.5" /> Revert file</Button>}
              {canReview && <Button size="sm" variant="outline" onClick={runReview} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Review with AI</Button>}
            </span>
          </div>
          <SqlEditor value={value} readOnly={!canEdit} rows={Math.min(34, Math.max(12, value.split("\n").length + 1))} label="File content"
                     onChange={(v) => setDrafts((d) => ({ ...d, [path]: v }))} />
          {!canEdit && <p className="text-xs text-muted-foreground">Read-only: {detail.latest ? "you need the DBT.EDIT privilege to propose changes" : "a newer generation exists"}.</p>}
          {canEdit && detail.edit_makes_version && <p className="text-xs text-muted-foreground">This generation is {detail.published ? "published" : "approved"}, so an approved change becomes generation v{detail.generation.generation_version + 1}; the run publishes the latest version.</p>}
          {review && review.ok && (
            <div className="surface space-y-2 p-3" data-testid="dbt-review">
              <p className="flex items-center gap-1.5 text-sm font-semibold"><Sparkles className="h-4 w-4 text-violet-600" /> AI review</p>
              <p className="text-sm">{review.data.summary}</p>
              <ul className="space-y-1 text-xs">
                {review.data.findings.map((f, i) => (
                  <li key={i}><span className={cn("mr-1 rounded px-1 py-0.5 text-[10px] font-semibold uppercase", f.severity === "error" ? "bg-rose-100 text-rose-700" : f.severity === "warning" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700")}>{f.severity}</span>
                    {f.message}{f.line_hint ? <span className="text-muted-foreground"> ({f.line_hint})</span> : null}</li>
                ))}
              </ul>
              {canEdit && review.data.revised_content && review.data.revised_content !== value && (
                <Button size="sm" variant="outline" onClick={() => setDrafts((d) => ({ ...d, [path]: review.data.revised_content }))}>Use the revised file</Button>
              )}
            </div>
          )}
        </section>
      </div>
      {canEdit && changed.length > 0 && (
        <div className="sticky bottom-4 z-30 mx-auto flex w-full max-w-3xl flex-wrap items-center gap-2 rounded-2xl border bg-card p-2.5 shadow-pop" data-testid="change-bar">
          <span className="flex items-center gap-2 pl-2 text-sm font-medium"><span className="h-2 w-2 rounded-full bg-warning" />{changed.length} file{changed.length === 1 ? "" : "s"} changed</span>
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What and why (shown to the reviewer)" className="h-8 min-w-[200px] flex-1 text-xs" aria-label="Change note" />
          <Button size="sm" variant="ghost" onClick={() => setDrafts({})}><RotateCcw className="h-3.5 w-3.5" /> Discard</Button>
          <Button size="sm" onClick={submit} disabled={pending || note.trim().length < 3} data-testid="submit-change">
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />} Submit for approval
          </Button>
        </div>
      )}
    </div>
  );
}
