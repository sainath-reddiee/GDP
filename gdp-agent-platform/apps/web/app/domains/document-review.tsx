"use client";

import { Fragment, useEffect, useMemo, useState, useTransition } from "react";
import { Check, Download, ExternalLink, FileText, ImageIcon, Loader2, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccess } from "@/components/access";
import { Drawer } from "@/components/hub/overlay";
import { prettyType } from "../knowledge/knowledge-ui";
import { decideInbox } from "../knowledge/actions";
import { documentReview, type DocumentReview as Review, type ReviewItem } from "./actions";

type Mark = { id: string; quote: string; n: number };
type Piece = { text: string; mark?: Mark };

/** Where each quote sits in the text: exact first, then ignoring case and runs of whitespace. Overlaps are dropped
 *  (the first quote keeps the passage). */
export function highlight(text: string, marks: Mark[]): Piece[] {
  const ranges: { start: number; end: number; mark: Mark }[] = [];
  for (const m of marks) {
    if (!m.quote.trim()) continue;
    let start = text.indexOf(m.quote);
    let end = start + m.quote.length;
    if (start < 0) {
      const words = m.quote.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      const found = new RegExp(words.join("\\s+"), "i").exec(text);
      if (!found) continue;
      start = found.index;
      end = start + found[0].length;
    }
    if (ranges.some((r) => start < r.end && end > r.start)) continue;
    ranges.push({ start, end, mark: m });
  }
  ranges.sort((a, b) => a.start - b.start);
  const out: Piece[] = [];
  let at = 0;
  for (const r of ranges) {
    if (r.start > at) out.push({ text: text.slice(at, r.start) });
    out.push({ text: text.slice(r.start, r.end), mark: r.mark });
    at = r.end;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

const STATUS = {
  PROPOSED: { label: "Waiting", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  ACTIVE: { label: "Accepted", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  REJECTED: { label: "Rejected", cls: "bg-muted text-muted-foreground" },
} as const;
const READ = {
  QUEUED: "Waiting to be read", READING: "Being read", READY: "Read", PARTIAL: "Partly read", NOT_READABLE: "Kept, not readable",
  FAILED: "Reading failed", INTERRUPTED: "Reading was interrupted",
} as const;

const size = (n?: number | null) => n == null ? "" : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

/** One document side by side with what was proposed from it: the text as it was read with every quoted passage
 *  marked and numbered, and the items with their quote, page and decision. A steward approves or rejects each item
 *  against the passage it comes from (never their own upload: the API refuses it and says why). */
export function DocumentReview({ domainId, sourceId, focus, open, onClose, onDecided }: {
  domainId: string; sourceId: string | null; focus?: string | null; open: boolean; onClose: () => void; onDecided?: () => void;
}) {
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [data, setData] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState<string>("");
  const [active, setActive] = useState<string | null>(focus ?? null);
  const [show, setShow] = useState<"PROPOSED" | "ACTIVE" | "REJECTED" | "ALL">("ALL");
  const [checked, setChecked] = useState<string[]>([]);
  const [refused, setRefused] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [pending, start] = useTransition();

  const load = () => {
    if (!sourceId) return;
    void documentReview(domainId, sourceId).then((r) => {
      if (!r.ok) { setError(r.error); return; }
      setData(r.data);
      const first = r.data.items.find((i) => i.knowledge_id === focus)?.page_id ?? r.data.pages[0]?.page_id ?? "";
      setPage((p) => p || first);
    });
  };
  useEffect(() => {
    if (!open) return;
    setData(null); setError(""); setPage(""); setChecked([]); setRefused({}); setMsg(""); setActive(focus ?? null);
    setShow(focus ? "ALL" : "PROPOSED");
    load();
  }, [open, sourceId, focus]);   // eslint-disable-line react-hooks/exhaustive-deps

  const current = data?.pages.find((p) => p.page_id === page) ?? data?.pages[0];
  const items = data?.items ?? [];
  const numbered = useMemo(() => new Map(items.map((it, i) => [it.knowledge_id, i + 1])), [items]);
  const onPage = items.filter((it) => it.page_id === current?.page_id || (data?.pages.length ?? 0) <= 1);
  const pieces = useMemo(() => current ? highlight(current.text, onPage.map((it) => ({ id: it.knowledge_id, quote: it.quote, n: numbered.get(it.knowledge_id) ?? 0 }))) : [],
    [current, onPage, numbered]);
  useEffect(() => {
    if (active) document.getElementById(`q-${active}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [active, pieces]);

  const decide = (ids: string[], decision: "approve" | "reject") => start(async () => {
    const r = await decideInbox(ids, decision);
    if (!r.ok) { setMsg(r.error); return; }
    const no = r.data.refused ?? [];
    setRefused((prev) => ({ ...Object.fromEntries(Object.entries(prev).filter(([k]) => !ids.includes(k))), ...Object.fromEntries(no.map((x) => [x.knowledge_id, x.reason])) }));
    setMsg(`${r.data.decided} ${decision === "approve" ? "approved" : "rejected"}${no.length ? `, ${no.length} refused (see the item)` : ""}.`);
    setChecked([]);
    load();
    onDecided?.();
  });

  const s = data?.source;
  const shown = items.filter((it) => show === "ALL" || it.status === show);
  const waiting = items.filter((it) => it.status === "PROPOSED");
  const isImage = (s?.content_type ?? "").startsWith("image/");
  const file = s && s.kind === "UPLOAD" && s.stage_path ? `/bff/domain-document/${encodeURIComponent(domainId)}/${encodeURIComponent(s.source_id)}` : null;

  return (
    <Drawer open={open} onClose={onClose} wide
            title={<span className="flex items-center gap-2"><FileText className="h-4 w-4 text-primary" />{s?.title ?? s?.file_name ?? "Document"}</span>}
            subtitle={s ? [s.kind === "UPLOAD" ? "Uploaded" : s.kind === "SPACE" ? "Confluence space" : "Confluence page",
                           s.read_status ? READ[s.read_status] : null, s.read_method, size(s.file_size), s.pages_count ? `${s.pages_count} pages` : null,
                           s.chars_total ? `${(s.chars_read ?? s.chars_total).toLocaleString()} of ${s.chars_total.toLocaleString()} characters read` : null,
                           s.created_by ? `uploaded by ${s.created_by}` : null].filter(Boolean).join(" · ") : "Loading"}
            actions={<div className="flex gap-2">
              {file && <a href={file} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted"><Download className="h-3.5 w-3.5" />Original</a>}
              {s?.url && <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted"><ExternalLink className="h-3.5 w-3.5" />Confluence</a>}
            </div>}>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!data && !error && <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the document…</p>}
      {data && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_400px]" data-testid="document-review">
          <section className="min-w-0 space-y-2">
            {s?.read_note && <p className="rounded-lg bg-muted/60 px-3 py-2 text-xs text-muted-foreground">{s.read_note}</p>}
            {isImage && file && (
              <div className="rounded-xl border p-2">
                <p className="mb-1 flex items-center gap-1 text-[11px] text-muted-foreground"><ImageIcon className="h-3.5 w-3.5" />The uploaded image</p>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`${file}?inline=1`} alt={s?.file_name ?? "uploaded image"} className="max-h-80 w-auto rounded-lg" />
              </div>
            )}
            {data.pages.length > 1 && (
              <select aria-label="Page" value={current?.page_id ?? ""} onChange={(e) => setPage(e.target.value)} className="h-8 rounded-lg border bg-background px-2 text-xs">
                {data.pages.map((p) => <option key={p.page_id} value={p.page_id}>{p.title}{p.version ? ` (version ${p.version})` : ""}</option>)}
              </select>
            )}
            <p className="text-[11px] text-muted-foreground">The text as the platform read it. Each marked passage is the quote an item was proposed from; the number matches the list.</p>
            {!current?.text ? <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No text was read from this document.</p> : (
              <div className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-xl border bg-background p-4 text-xs leading-relaxed" data-testid="document-text">
                {pieces.map((p, i) => p.mark ? (
                  <mark key={i} id={`q-${p.mark.id}`} onClick={() => setActive(p.mark!.id)}
                        className={cn("cursor-pointer rounded px-0.5 text-foreground", active === p.mark.id ? "bg-primary/30 ring-2 ring-primary" : "bg-amber-200/60 dark:bg-amber-500/30")}>
                    <sup className="mr-0.5 font-semibold text-primary">{p.mark.n}</sup>{p.text}
                  </mark>
                ) : <Fragment key={i}>{p.text}</Fragment>)}
                {current.truncated && <p className="mt-2 text-muted-foreground">(the rest of the text is not shown here)</p>}
              </div>
            )}
          </section>
          <section className="min-w-0 space-y-2">
            <div className="flex flex-wrap gap-1 text-xs" role="group" aria-label="Show">
              {(["PROPOSED", "ACTIVE", "REJECTED", "ALL"] as const).map((k) => (
                <button key={k} type="button" aria-pressed={show === k} onClick={() => setShow(k)}
                        className={cn("rounded-full border px-2.5 py-0.5", show === k ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>
                  {k === "ALL" ? `All ${items.length}` : `${STATUS[k].label} ${data.counts[k]}`}
                </button>
              ))}
            </div>
            {may && waiting.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/50 px-2 py-1.5 text-xs">
                <label className="flex items-center gap-1"><input type="checkbox" checked={checked.length > 0 && checked.length === waiting.length}
                  onChange={(e) => setChecked(e.target.checked ? waiting.map((w) => w.knowledge_id) : [])} />{checked.length} checked</label>
                <button type="button" disabled={!checked.length || pending} onClick={() => decide(checked, "approve")}
                        className="ml-auto inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 font-medium text-primary-foreground disabled:opacity-50"><Check className="h-3 w-3" />Approve checked</button>
                <button type="button" disabled={!checked.length || pending} onClick={() => decide(checked, "reject")}
                        className="inline-flex items-center gap-1 rounded-md border px-2 py-1 disabled:opacity-50"><X className="h-3 w-3" />Reject checked</button>
              </div>
            )}
            {msg && <p role="status" className="text-xs text-muted-foreground">{msg}</p>}
            {!shown.length && <p className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">Nothing here.</p>}
            <ol className="max-h-[70vh] space-y-2 overflow-auto pr-1">
              {shown.map((it) => <Item key={it.knowledge_id} it={it} n={numbered.get(it.knowledge_id) ?? 0} active={active === it.knowledge_id}
                                       may={may} pending={pending} checked={checked.includes(it.knowledge_id)} refused={refused[it.knowledge_id]}
                                       onFocus={() => { if (it.page_id && it.page_id !== current?.page_id) setPage(it.page_id); setActive(it.knowledge_id); }}
                                       onCheck={(on) => setChecked(on ? [...checked, it.knowledge_id] : checked.filter((x) => x !== it.knowledge_id))}
                                       onDecide={(d) => decide([it.knowledge_id], d)} />)}
            </ol>
          </section>
        </div>
      )}
    </Drawer>
  );
}

function Item({ it, n, active, may, pending, checked, refused, onFocus, onCheck, onDecide }: {
  it: ReviewItem; n: number; active: boolean; may: boolean; pending: boolean; checked: boolean; refused?: string;
  onFocus: () => void; onCheck: (on: boolean) => void; onDecide: (d: "approve" | "reject") => void;
}) {
  const st = STATUS[it.status];
  return (
    <li className={cn("rounded-xl border p-3 text-xs", active && "border-primary ring-1 ring-primary/30")}>
      <div className="flex items-start gap-2">
        {it.status === "PROPOSED" && may && <input type="checkbox" className="mt-0.5" aria-label={`Check ${it.title}`} checked={checked} onChange={(e) => onCheck(e.target.checked)} />}
        <button type="button" onClick={onFocus} className="min-w-0 flex-1 text-left">
          <p className="font-semibold"><span className="mr-1 text-primary">{n}.</span>{it.title}</p>
          <p className="text-[11px] text-muted-foreground">{prettyType(it.knowledge_type)}
            {it.target_table ? ` · ${it.target_table}${it.target_column ? `.${it.target_column}` : ""}` : ""}
            {it.page ? ` · page ${it.page}` : ""}{it.confidence != null ? ` · confidence ${Math.round(Number(it.confidence) * 100)}%` : ""}</p>
        </button>
        <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium", st.cls)}>{st.label}</span>
      </div>
      <p className="mt-1.5 line-clamp-4 whitespace-pre-wrap">{it.content.split("\n\nSource:")[0]}</p>
      {it.quote && (
        <blockquote className="mt-1.5 border-l-2 border-amber-400 pl-2 italic text-muted-foreground">&ldquo;{it.quote}&rdquo;</blockquote>
      )}
      {!it.found && it.quote && (
        <p className="mt-1 flex items-center gap-1 text-amber-600"><TriangleAlert className="h-3 w-3" />This quote is not in the text read: check it carefully before approving.</p>
      )}
      {it.reviewed_by && it.status !== "PROPOSED" && <p className="mt-1 text-[11px] text-muted-foreground">{it.status === "ACTIVE" ? "Approved" : "Rejected"} by {it.reviewed_by}{it.review_note ? `: ${it.review_note}` : ""}</p>}
      {refused && <p role="alert" className="mt-1 text-destructive">Refused: {refused}</p>}
      {it.status === "PROPOSED" && may && (
        <div className="mt-2 flex gap-1.5">
          <button type="button" disabled={pending} onClick={() => onDecide("approve")} className="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 font-medium text-primary-foreground disabled:opacity-50"><Check className="h-3 w-3" />Approve</button>
          <button type="button" disabled={pending} onClick={() => onDecide("reject")} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 disabled:opacity-50"><X className="h-3 w-3" />Reject</button>
        </div>
      )}
    </li>
  );
}
