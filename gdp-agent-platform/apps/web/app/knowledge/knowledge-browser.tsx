"use client";

import { useState, useTransition } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Archive, BadgeCheck, Loader2, Lock, Plus, Save, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import { addKnowledge, editKnowledge, setKnowledgeStatus, verifyKnowledge, type ItemInput, type KnowledgeItem } from "./actions";
import { KnowledgeDrawer } from "./item-drawer";
import { OriginChip, prettyType, StatusPill, TypeIcon, Verified } from "./knowledge-ui";
import { useScrollLock } from "@/components/use-scroll-lock";

export const TYPES = ["GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MAPPING_PATTERN", "MODEL_DEFINITION",
  "NAMING_STANDARD", "DBT_PATTERN", "SODA_PATTERN", "EXCEPTION", "STTM_TEMPLATE", "ONBOARDING_GUIDE", "COLUMN_RULE", "QA_TEST"];

type Domain = { domain_id: string; domain_name: string };

const TEMPLATE: Record<string, Record<string, unknown>> = {
  GLOSSARY: { target_column: "", synonyms: [] },
  BUSINESS_RULE: { target_column: "", rule: "" },
  TRANSFORMATION_RULE: { target_column: "", expression: "TRIM({col})" },
  MAPPING_PATTERN: { source_column: "", target_column: "" },
};

/** Browse knowledge in use; open an item for its versions, provenance and usage; add, edit, verify and retire. */
export function KnowledgeBrowser({ items, total, domains, offset, limit }: {
  items: KnowledgeItem[]; total: number; domains: Domain[]; offset: number; limit: number;
}) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const { canAct } = useAccess();
  const mayEdit = canAct("KNOWLEDGE.EDIT");
  const [editing, setEditing] = useState<KnowledgeItem | "new" | null>(null);
  const [open, setOpen] = useState<KnowledgeItem | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [msg, setMsg] = useState("");
  const [pending, start] = useTransition();

  const go = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!("offset" in changes)) next.delete("offset");
    router.push(`${path}?${next.toString()}`);
  };
  const bulk = (action: "verify" | "retire") => start(async () => {
    const chosen = items.filter((i) => selected.includes(i.knowledge_id) && i.editable);
    let failed = "";
    for (const i of chosen) {
      const r = action === "verify" ? await verifyKnowledge(i.knowledge_id) : await setKnowledgeStatus(i.knowledge_id, "retire");
      if (!r.ok) { failed = r.error; break; }
    }
    setMsg(failed || `${chosen.length} item${chosen.length === 1 ? "" : "s"} ${action === "verify" ? "verified" : "retired"}`);
    setSelected([]);
    router.refresh();
  });

  const select = "h-9 rounded-lg border bg-card px-2 text-sm";
  const all = items.length > 0 && items.every((i) => selected.includes(i.knowledge_id));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2 shadow-sm">
        <form className="relative min-w-[220px] flex-1" onSubmit={(e) => { e.preventDefault(); go({ q: q.trim() || null }); }}>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by title, content or key"
                 aria-label="Filter knowledge" className="h-9 w-full rounded-lg bg-transparent pl-8 pr-3 text-sm outline-none" />
        </form>
        <select aria-label="Domain" className={select} value={params.get("domain_id") ?? ""} onChange={(e) => go({ domain_id: e.target.value || null })}>
          <option value="">All domains</option>
          {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
        </select>
        <select aria-label="Type" className={select} value={params.get("type") ?? ""} onChange={(e) => go({ type: e.target.value || null })}>
          <option value="">All types</option>
          {TYPES.map((t) => <option key={t} value={t}>{prettyType(t)}</option>)}
        </select>
        <select aria-label="Status" className={select} value={params.get("status") ?? "ACTIVE"} onChange={(e) => go({ status: e.target.value === "ACTIVE" ? null : e.target.value })}>
          <option value="ACTIVE">In use</option>
          <option value="RETIRED">Retired</option>
          <option value="DRAFT">Draft or rejected</option>
          <option value="ALL">Any status</option>
        </select>
        {mayEdit && <Button size="sm" onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add knowledge</Button>}
      </div>

      {selected.length > 0 && mayEdit && (
        <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
          <span className="font-medium">{selected.length} selected</span>
          <Button size="sm" variant="outline" disabled={pending} onClick={() => bulk("verify")}><BadgeCheck className="h-3.5 w-3.5" />Mark verified</Button>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => bulk("retire")}><Archive className="h-3.5 w-3.5" />Retire</Button>
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          <button type="button" className="ml-auto text-xs text-muted-foreground hover:text-foreground" onClick={() => setSelected([])}>Clear</button>
        </div>
      )}
      {msg && <p role="status" className="text-sm text-muted-foreground">{msg}</p>}

      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No knowledge matches these filters.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-8 px-3 py-2">{mayEdit && <input type="checkbox" aria-label="Select all" checked={all} onChange={() => setSelected(all ? [] : items.map((i) => i.knowledge_id))} />}</th>
                <th className="py-2">Item</th><th className="px-2 py-2">Domain</th><th className="px-2 py-2">Origin</th>
                <th className="px-2 py-2">Version</th><th className="px-3 py-2 text-right">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map((item) => (
                <tr key={item.knowledge_id} className="cursor-pointer hover:bg-muted/30" onClick={() => setOpen(item)}>
                  <td className="px-3 py-2.5 align-top" onClick={(e) => e.stopPropagation()}>
                    {mayEdit && <input type="checkbox" aria-label={`Select ${item.title}`} checked={selected.includes(item.knowledge_id)}
                                       onChange={() => setSelected(selected.includes(item.knowledge_id) ? selected.filter((x) => x !== item.knowledge_id) : [...selected, item.knowledge_id])} />}
                  </td>
                  <td className="py-2.5 pr-2">
                    <div className="flex items-start gap-2.5">
                      <TypeIcon type={item.knowledge_type} />
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-1.5 font-medium">{item.title}<Verified by={item.verified_by} at={item.verified_at} />
                          {item.status !== "ACTIVE" && <StatusPill status={item.status} />}
                          {!item.editable && <span title={item.read_only_reason ?? ""}><Lock className="h-3 w-3 text-muted-foreground" /></span>}</p>
                        <p className="line-clamp-1 text-xs text-muted-foreground">{prettyType(item.knowledge_type)} · {item.content}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-2 py-2.5 align-top text-xs text-muted-foreground">{item.domain_name}</td>
                  <td className="px-2 py-2.5 align-top"><OriginChip origin={item.origin} short /></td>
                  <td className="px-2 py-2.5 align-top text-xs tabular-nums">v{item.version}</td>
                  <td className="px-3 py-2.5 text-right align-top text-xs text-muted-foreground">{ago(item.updated_at ?? item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}</span>
        <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => go({ offset: String(Math.max(0, offset - limit)) })}>Previous</Button>
        <Button size="sm" variant="ghost" disabled={offset + limit >= total} onClick={() => go({ offset: String(offset + limit) })}>Next</Button>
      </div>

      {open && <KnowledgeDrawer item={open} onClose={() => setOpen(null)} onEdit={(i) => { setOpen(null); setEditing(i); }} />}
      {editing && (
        <ItemEditor domains={domains} item={editing === "new" ? null : editing}
                    onClose={() => setEditing(null)} onSaved={() => { setEditing(null); router.refresh(); }} />
      )}
    </div>
  );
}

export function ItemEditor({ domains, item, onClose, onSaved }: {
  domains: Domain[]; item: KnowledgeItem | null; onClose: () => void; onSaved: () => void;
}) {
  useScrollLock();
  const [domainId, setDomainId] = useState(item?.domain_id ?? domains[0]?.domain_id ?? "");
  const [type, setType] = useState(item?.knowledge_type ?? "GLOSSARY");
  const [title, setTitle] = useState(item?.title ?? "");
  const [content, setContent] = useState(item?.content ?? "");
  const [note, setNote] = useState("");
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
    const input: ItemInput = { domain_id: domainId, knowledge_type: type, title, content, content_json: contentJson, change_note: note.trim() || null };
    const r = item ? await editKnowledge(item.knowledge_id, input) : await addKnowledge(input);
    if (!r.ok) { setError(r.error); return; }
    onSaved();
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label={item ? "Edit knowledge" : "Add knowledge"}
             className="relative flex h-full w-[560px] max-w-full flex-col gap-3 overflow-y-auto overscroll-contain border-l bg-background p-5 shadow-2xl">
        <h3 className="text-base font-semibold">{item ? `Edit (saves version ${item.version + 1}, v${item.version} stays in history)` : "Add knowledge"}</h3>
        <label className="text-xs font-medium">Domain
          <select value={domainId} disabled={!!item} onChange={(e) => setDomainId(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium">Type
          <select value={type} onChange={(e) => { setType(e.target.value); if (!item) setStructured(JSON.stringify(TEMPLATE[e.target.value] ?? {}, null, 2)); }}
                  className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
            {TYPES.map((t) => <option key={t} value={t}>{prettyType(t)}</option>)}
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
        <label className="text-xs font-medium">What changed and why (optional, shown in the version history)
          <input value={note} onChange={(e) => setNote(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm" />
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
