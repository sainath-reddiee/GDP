"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Download, ExternalLink, Eye, FileInput, FileText, Link2, Loader2, Network, RefreshCw, Trash2, Upload, Wand2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import {
  domainDataModel, domainDocuments, linkConfluence, refreshDocument, removeDocument, uploadDomainDocuments,
  type DomainDataModel, type DomainDocuments,
} from "../actions";
import { DocumentReview } from "../document-review";

type Msg = { tone: "ok" | "error"; text: string } | null;
const note = (m: Msg) => m && (
  <p role={m.tone === "error" ? "alert" : "status"} className={cn("rounded-lg px-3 py-2 text-xs", m.tone === "error" ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success")}>{m.text}</p>
);

const READ_LABEL: Record<string, { label: string; cls: string }> = {
  QUEUED: { label: "Waiting to be read", cls: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  READING: { label: "Being read", cls: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  READY: { label: "Read", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  PARTIAL: { label: "Partly read", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  NOT_READABLE: { label: "Kept, not readable", cls: "bg-muted text-muted-foreground" },
  FAILED: { label: "Reading failed", cls: "bg-destructive/10 text-destructive" },
  INTERRUPTED: { label: "Reading interrupted", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
};
const KIND_LABEL: Record<string, string> = { text: "Text", excel: "Excel", document: "Document", image: "Image", binary: "File" };
const fileSize = (n?: number | null) => n == null ? "" : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

/** Confluence pages and spaces linked to the domain, and uploaded documents of any type: each original is kept, read
 *  in the background, and the knowledge it states is proposed for review side by side with the passage it comes from. */
function DocumentsPanel({ domainId }: { domainId: string }) {
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [data, setData] = useState<DomainDocuments | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [review, setReview] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = () => void domainDocuments(domainId).then((r) => (r.ok ? setData(r.data) : setMsg({ tone: "error", text: r.error })));
  useEffect(load, [domainId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const reading = (data?.sources ?? []).some((s) => s.read_status === "QUEUED" || s.read_status === "READING");
  useEffect(() => {   // follow documents being read
    if (!reading) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [reading, domainId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const done = (text: string) => { setMsg({ tone: "ok", text }); load(); };
  const link = async () => {
    if (!url.trim()) return;
    setBusy("link"); setMsg(null);
    const r = await linkConfluence(domainId, url.trim()).catch(() => null);
    setBusy(null);
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Could not link the page." }); return; }
    setUrl("");
    done(`Linked ${r.data.title}: ${r.data.result.read} page${r.data.result.read === 1 ? "" : "s"} read, ${r.data.result.proposed} knowledge items proposed for review.`);
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const limit = data?.limits?.max_mb ?? 50;
    const list = Array.from(files).slice(0, data?.limits?.max_files ?? 10);
    const lines: string[] = [];
    setMsg(null);
    for (const [i, f] of list.entries()) {   // one request per file, so a large file never shares a request's size limit
      setBusy(`upload:${i + 1}/${list.length}`);
      if (f.size > limit * 1024 * 1024) { lines.push(`${f.name}: larger than ${limit} MB`); continue; }
      const form = new FormData();
      form.append("files", f);
      const r = await uploadDomainDocuments(domainId, form).catch(() => null);
      if (!r?.ok) { lines.push(`${f.name}: ${r && !r.ok ? r.error : "could not be uploaded"}`); continue; }
      for (const d of r.data.documents) lines.push(d.error ? `${d.file_name}: ${d.error}` : d.duplicate ? `${d.file_name}: ${d.note}` : `${d.file_name}: kept, being read${d.note ? ` (${d.note})` : ""}`);
    }
    setBusy(null);
    if (file.current) file.current.value = "";
    if (files.length > list.length) lines.push(`${files.length - list.length} more file(s) not uploaded: at most ${list.length} at a time`);
    setMsg({ tone: lines.some((l) => /larger|could not|needs|not uploaded|error/i.test(l)) ? "error" : "ok", text: lines.join(". ") + "." });
    load();
  };
  const act = async (id: string, kind: "refresh" | "remove") => {
    setBusy(id); setMsg(null);
    const r = await (kind === "refresh" ? refreshDocument(domainId, id) : removeDocument(domainId, id)).catch(() => null);
    setBusy(null);
    if (!r?.ok) { setMsg({ tone: "error", text: r && !r.ok ? r.error : "Failed." }); return; }
    if (kind === "remove") return done("Unlinked. Knowledge already accepted from it stays; the file stays in the document stage.");
    const x = r.data as { read?: number; unchanged?: number; proposed?: number; read_status?: string };
    done(x.read_status ? "Reading it again from the kept file." : `${x.read ?? 0} changed page${x.read === 1 ? "" : "s"} read, ${x.unchanged ?? 0} unchanged, ${x.proposed ?? 0} items proposed.`);
  };
  if (!data) return <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Loading documents…</p>;
  const c = data.confluence;
  const canLink = may && c.enabled && c.connected;
  return (
    <section className="surface space-y-3 p-4" data-testid="domain-documents">
      <div>
        <h3 className="text-sm font-semibold">Documents</h3>
        <p className="text-xs text-muted-foreground">Upload any file (PDF, Word, Excel, PowerPoint, images, text) or link Confluence. Each original is kept in
          Snowflake, read in the background (Cortex reads PDFs, slides and scans; an image model reads diagrams and screenshots), and the AI
          proposes the knowledge it states with the passage it comes from. Nothing is used until a steward approves it: open Review to check
          each item against its passage.</p>
      </div>
      {!data.deployed && <p className="text-xs text-amber-600">Documents need migration V051.</p>}
      {data.deployed && data.files === false && <p className="text-xs text-amber-600">Keeping originals and reading any file type need migration V052.</p>}
      {note(msg)}
      {may && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-[18rem] flex-1 items-center gap-2">
            <input aria-label="Confluence link" value={url} onChange={(e) => setUrl(e.target.value)} disabled={!canLink || !!busy}
                   placeholder={!c.enabled ? "Confluence is not enabled (Admin, Integrations, Jira)" : !c.connected ? "Connect Jira on Tickets first" : "Paste a Confluence page or space link"}
                   className="h-9 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm disabled:opacity-60" />
            <button type="button" onClick={() => void link()} disabled={!canLink || !url.trim() || !!busy}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {busy === "link" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />} Link
            </button>
          </div>
          <input ref={file} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} data-testid="document-upload" />
          <button type="button" onClick={() => file.current?.click()} disabled={!!busy || !data.deployed || data.files === false}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted disabled:opacity-50">
            {busy?.startsWith("upload") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            {busy?.startsWith("upload") ? `Uploading ${busy.split(":")[1]}` : "Upload"}
          </button>
        </div>
      )}
      {c.enabled && c.connected && !c.granted && <p className="text-xs text-amber-600">Your Jira sign-in does not include Confluence yet: connect Jira again on the Tickets page to grant it.</p>}
      {busy === "link" && <p className="text-xs text-muted-foreground"><Wand2 className="mr-1 inline h-3 w-3" />Reading and proposing knowledge; a space can take a minute.</p>}
      {!data.sources.length ? <p className="text-xs text-muted-foreground"><FileText className="mr-1 inline h-3.5 w-3.5" />No documents linked yet.</p> : (
        <ul className="divide-y divide-border/60">
          {data.sources.map((s) => {
            const st = s.read_status ? READ_LABEL[s.read_status] : null;
            const proposed = s.pages.reduce((n, p) => n + (p.proposed || 0), 0) || s.last_result?.proposed || 0;
            const kept = s.kind === "UPLOAD" && !!s.stage_path;
            return (
              <li key={s.source_id} className="space-y-1 py-2 text-xs" data-testid="document-row">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", s.kind === "UPLOAD" ? "bg-cyan-500/10 text-cyan-700" : "bg-blue-500/10 text-blue-700")}>
                    {s.kind === "UPLOAD" ? (KIND_LABEL[s.file_kind ?? ""] ?? "Uploaded") : s.kind === "SPACE" ? "Confluence space" : "Confluence page"}</span>
                  <button type="button" onClick={() => setReview(s.source_id)} className="font-medium hover:text-primary hover:underline">{s.title ?? s.file_name}</button>
                  {s.url && <a href={s.url} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground" aria-label="Open in Confluence"><ExternalLink className="h-3 w-3" /></a>}
                  {st && <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", st.cls)}>
                    {(s.read_status === "QUEUED" || s.read_status === "READING") && <Loader2 className="mr-1 inline h-2.5 w-2.5 animate-spin" />}{st.label}</span>}
                  <span className="text-muted-foreground">
                    {[fileSize(s.file_size), s.pages_count ? `${s.pages_count} pages` : null,
                      s.chars_total ? `${(s.chars_read ?? s.chars_total).toLocaleString()} of ${s.chars_total.toLocaleString()} characters` : null,
                      `${proposed} proposed`, s.created_by ? `by ${s.created_by}` : null, s.read_finished_at ?? s.last_refreshed_at].filter(Boolean).join(" · ")}
                  </span>
                  <span className="ml-auto flex gap-1">
                    <button type="button" onClick={() => setReview(s.source_id)} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium text-primary hover:bg-primary/10"><Eye className="h-3 w-3" /> Review</button>
                    {kept && <a href={`/bff/domain-document/${encodeURIComponent(domainId)}/${encodeURIComponent(s.source_id)}`} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted"><Download className="h-3 w-3" /> Original</a>}
                    {may && (s.kind !== "UPLOAD" || kept) && (
                      <button type="button" onClick={() => void act(s.source_id, "refresh")} disabled={!!busy || s.read_status === "QUEUED" || s.read_status === "READING"}
                              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted disabled:opacity-50">
                        <RefreshCw className={cn("h-3 w-3", busy === s.source_id && "animate-spin")} /> {s.kind === "UPLOAD" ? "Read again" : "Refresh"}</button>
                    )}
                    {may && <button type="button" onClick={() => void act(s.source_id, "remove")} disabled={!!busy} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-3 w-3" /> Unlink</button>}
                  </span>
                </div>
                {s.read_method && <p className="pl-3 text-muted-foreground">Read with {s.read_method}</p>}
                {s.read_note && s.read_status !== "READY" && <p className={cn("pl-3", s.read_status === "FAILED" ? "text-destructive" : "text-muted-foreground")}>{s.read_note}</p>}
                {s.kind !== "UPLOAD" && !!s.pages.length && (
                  <ul className="space-y-0.5 pl-3 text-muted-foreground">
                    {s.pages.slice(0, 10).map((p) => <li key={p.page_id}>{p.title}{p.version ? ` · version ${p.version}` : ""} · {p.proposed} proposed · {(p.chars ?? 0).toLocaleString()} characters</li>)}
                    {s.pages.length > 10 && <li>+{s.pages.length - 10} more pages</li>}
                  </ul>
                )}
                {!!s.last_result?.errors?.length && <p className="pl-3 text-destructive">{s.last_result.errors.length} page(s) could not be read: {s.last_result.errors[0].error}</p>}
              </li>
            );
          })}
        </ul>
      )}
      <DocumentReview domainId={domainId} sourceId={review} open={!!review} onClose={() => setReview(null)} onDecided={load} />
    </section>
  );
}

/** The domain's data model as a source: which version runs use, and where to bring in a model file. */
function DataModelPanel({ domainId }: { domainId: string }) {
  const { canAct } = useAccess();
  const [data, setData] = useState<DomainDataModel | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void domainDataModel(domainId).then((r) => (r.ok ? setData(r.data) : setError(r.error))); }, [domainId]);
  const m = data?.model;
  const d = data?.diagram;
  const workbench = m ? `/dashboard/modeling/${encodeURIComponent(m.model_id)}` : "/dashboard/modeling";
  return (
    <section className="surface space-y-3 p-4" data-testid="domain-model-source">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Network className="h-4 w-4 text-primary" />Data model</h3>
        <p className="text-xs text-muted-foreground">The tables every run maps into. Bring in a model file (an Erwin model saved as XML, its DDL, or a data
          dictionary in Excel or CSV): it is merged into a draft in the Model Workbench, where the changes are reviewed and approved before any run uses them.</p>
      </div>
      {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : !data ? (
        <p className="text-xs text-muted-foreground"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />Reading the model…</p>
      ) : (
        <p className="text-xs">
          {!m ? "No data model yet." : <>
            {m.approved_version ? <span className="font-medium">Approved v{m.approved_version}</span> : <span className="font-medium">Not approved yet</span>}
            {d && <span className="text-muted-foreground"> · {d.summary.tables} tables · {d.summary.columns} columns · {d.summary.relationships} relationships</span>}
            {m.open_version && <span className="text-amber-600"> · draft v{m.open_version} open</span>}
          </>}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {canAct("MODEL.EDIT") && (
          <Link href={`${workbench}?import=file`} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground">
            <FileInput className="h-4 w-4" />Import a model file</Link>
        )}
        <Link href={`/domains/${encodeURIComponent(domainId)}?tab=lineage`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm hover:bg-muted">
          See the diagram<ArrowRight className="h-4 w-4" /></Link>
      </div>
    </section>
  );
}

/** The Sources tab: what this domain is defined by beyond its pack: its data model, its documents and its own skills. */
export function SourcesTab({ domainId }: { domainId: string }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="space-y-4">
        <DataModelPanel domainId={domainId} />
        <DocumentsPanel domainId={domainId} />
      </div>
    </div>
  );
}
