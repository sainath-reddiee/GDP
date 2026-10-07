"use client";

import { useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Archive, History, Loader2, Lock, Pencil, Plus, RotateCcw, Save, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  addKnowledge, editKnowledge, knowledgeHistory, setKnowledgeStatus, type ItemInput, type KnowledgeItem,
} from "./actions";

export const TYPES = ["GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MAPPING_PATTERN", "MODEL_DEFINITION",
  "NAMING_STANDARD", "DBT_PATTERN", "SODA_PATTERN", "EXCEPTION", "STTM_TEMPLATE", "ONBOARDING_GUIDE", "COLUMN_RULE"];

type Domain = { domain_id: string; domain_name: string };

const TEMPLATE: Record<string, Record<string, unknown>> = {
  GLOSSARY: { target_column: "", synonyms: [] },
  BUSINESS_RULE: { target_column: "", rule: "" },
  TRANSFORMATION_RULE: { target_column: "", expression: "TRIM({col})" },
  MAPPING_PATTERN: { source_column: "", target_column: "" },
};

/** Browse, add, edit (new version), retire and restore knowledge items, with history. */
export function KnowledgeBrowser({ items, total, domains, offset, limit }: {
  items: KnowledgeItem[]; total: number; domains: Domain[]; offset: number; limit: number;
}) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [editing, setEditing] = useState<KnowledgeItem | "new" | null>(null);
  const [history, setHistory] = useState<{ id: string; versions: KnowledgeItem[] } | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const go = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!("offset" in changes)) next.delete("offset");
    router.push(`${path}?${next.toString()}`);
  };

  const status = (item: KnowledgeItem, action: "retire" | "restore") => start(async () => {
    setError("");
    const r = await setKnowledgeStatus(item.knowledge_id, action);
    if (!r.ok) setError(r.error); else router.refresh();
  });

  const showHistory = (item: KnowledgeItem) => start(async () => {
    setError("");
    const r = await knowledgeHistory(item.knowledge_id);
    if (r.ok) setHistory({ id: item.knowledge_id, versions: r.data.versions }); else setError(r.error);
  });

  const select = "h-9 rounded-md border bg-card px-2 text-sm";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); go({ q: q.trim() || null }); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by title, content or key"
                 aria-label="Filter knowledge" className="h-9 w-64 rounded-md border bg-card px-3 text-sm" />
        </form>
        <select aria-label="Domain" className={select} value={params.get("domain_id") ?? ""} onChange={(e) => go({ domain_id: e.target.value || null })}>
          <option value="">All domains</option>
          {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
        </select>
        <select aria-label="Type" className={select} value={params.get("type") ?? ""} onChange={(e) => go({ type: e.target.value || null })}>
          <option value="">All types</option>
          {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").toLowerCase()}</option>)}
        </select>
        <select aria-label="Status" className={select} value={params.get("status") ?? "ACTIVE"} onChange={(e) => go({ status: e.target.value === "ACTIVE" ? null : e.target.value })}>
          <option value="ACTIVE">Active</option>
          <option value="RETIRED">Retired</option>
          <option value="DRAFT">Draft</option>
          <option value="ALL">Any status</option>
        </select>
        <Button size="sm" className="ml-auto" onClick={() => setEditing("new")}><Plus className="h-4 w-4" /> Add knowledge</Button>
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No knowledge matches these filters.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {items.map((item) => (
            <li key={item.knowledge_id} className="flex flex-wrap items-start gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  {item.title}
                  <Badge variant="outline">{item.knowledge_type.replace(/_/g, " ").toLowerCase()}</Badge>
                  {item.status !== "ACTIVE" && <Badge variant="secondary">{item.status.toLowerCase()}</Badge>}
                  {!item.editable && <span title={item.read_only_reason ?? ""}><Lock className="h-3.5 w-3.5 text-muted-foreground" /></span>}
                </p>
                <p className="line-clamp-2 text-xs text-muted-foreground">{item.content}</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {item.domain_name} · v{item.version} · {item.created_by} · {(item.updated_at ?? item.created_at)?.slice(0, 16)}
                </p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="ghost" title="History" onClick={() => showHistory(item)}><History className="h-3.5 w-3.5" /></Button>
                {item.editable && (
                  <>
                    <Button size="sm" variant="ghost" title="Edit" onClick={() => setEditing(item)}><Pencil className="h-3.5 w-3.5" /></Button>
                    {item.status === "RETIRED"
                      ? <Button size="sm" variant="ghost" title="Restore" disabled={pending} onClick={() => status(item, "restore")}><RotateCcw className="h-3.5 w-3.5" /></Button>
                      : <Button size="sm" variant="ghost" title="Retire" disabled={pending} onClick={() => status(item, "retire")}><Archive className="h-3.5 w-3.5" /></Button>}
                  </>
                )}
              </div>
              {history?.id === item.knowledge_id && (
                <div className="w-full rounded-lg bg-muted/40 p-2 text-xs">
                  <div className="mb-1 flex items-center justify-between font-medium">
                    {history.versions.length} version(s)
                    <button type="button" aria-label="Close history" onClick={() => setHistory(null)}><X className="h-3.5 w-3.5" /></button>
                  </div>
                  <ol className="space-y-1">
                    {history.versions.map((v) => (
                      <li key={v.knowledge_id}>
                        <span className="font-mono">v{v.version}</span> · {v.created_by} · {v.created_at?.slice(0, 16)}
                        {v.is_current ? " · current" : ""}: <span className="text-muted-foreground">{v.content.slice(0, 160)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}</span>
        <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => go({ offset: String(Math.max(0, offset - limit)) })}>Previous</Button>
        <Button size="sm" variant="ghost" disabled={offset + limit >= total} onClick={() => go({ offset: String(offset + limit) })}>Next</Button>
      </div>

      {editing && (
        <ItemEditor domains={domains} item={editing === "new" ? null : editing}
                    onClose={() => setEditing(null)} onSaved={() => { setEditing(null); router.refresh(); }} />
      )}
    </div>
  );
}

function ItemEditor({ domains, item, onClose, onSaved }: {
  domains: Domain[]; item: KnowledgeItem | null; onClose: () => void; onSaved: () => void;
}) {
  const [domainId, setDomainId] = useState(item?.domain_id ?? domains[0]?.domain_id ?? "");
  const [type, setType] = useState(item?.knowledge_type ?? "GLOSSARY");
  const [title, setTitle] = useState(item?.title ?? "");
  const [content, setContent] = useState(item?.content ?? "");
  const [structured, setStructured] = useState(
    item?.content_json ? JSON.stringify(item.content_json, null, 2) : JSON.stringify(TEMPLATE[item?.knowledge_type ?? "GLOSSARY"] ?? {}, null, 2));
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const save = () => start(async () => {
    setError("");
    let contentJson: unknown = null;
    if (structured.trim() && structured.trim() !== "{}") {
      try { contentJson = JSON.parse(structured); } catch { setError("Structured content is not valid JSON."); return; }
    }
    const input: ItemInput = { domain_id: domainId, knowledge_type: type, title, content, content_json: contentJson };
    const r = item ? await editKnowledge(item.knowledge_id, input) : await addKnowledge(input);
    if (!r.ok) { setError(r.error); return; }
    onSaved();
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label={item ? "Edit knowledge" : "Add knowledge"}
             className="relative flex h-full w-[560px] max-w-full flex-col gap-3 overflow-y-auto border-l bg-background p-5 shadow-2xl">
        <h3 className="text-base font-semibold">{item ? `Edit (saves version ${item.version + 1})` : "Add knowledge"}</h3>
        <label className="text-xs font-medium">Domain
          <select value={domainId} disabled={!!item} onChange={(e) => setDomainId(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium">Type
          <select value={type} onChange={(e) => { setType(e.target.value); if (!item) setStructured(JSON.stringify(TEMPLATE[e.target.value] ?? {}, null, 2)); }}
                  className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
            {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").toLowerCase()}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium">Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm" />
        </label>
        <label className="text-xs font-medium">Content (what people and search read)
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={5} className="mt-1 w-full rounded-md border bg-card p-2 text-sm" />
        </label>
        <label className="text-xs font-medium">
          Structured content (what mapping, STTM and quality read){TEMPLATE[type] ? "" : " (optional for this type)"}
          <textarea value={structured} onChange={(e) => setStructured(e.target.value)} rows={7} spellCheck={false}
                    className={cn("mt-1 w-full rounded-md border bg-card p-2 font-mono text-xs")} />
          {type === "TRANSFORMATION_RULE" && <span className="text-[11px] text-muted-foreground">Write the source column as {"{col}"}.</span>}
        </label>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="mt-auto flex gap-2">
          <Button disabled={pending || !title.trim() || !content.trim() || !domainId} onClick={save}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save
          </Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </aside>
    </div>
  );
}
