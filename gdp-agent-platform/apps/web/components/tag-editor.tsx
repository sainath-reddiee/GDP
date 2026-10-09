"use client";

import { useAccess } from "@/components/access";
import { useEffect, useRef, useState, useTransition } from "react";
import { Loader2, Plus, Tag, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { loadEntityTags, loadTags, saveEntityTags, type TagInfo, type TagType } from "@/app/tag-actions";

export const TAG_TONE: Record<string, string> = {
  slate: "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200",
  blue: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  amber: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  red: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300",
  violet: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-300",
  pink: "border-pink-200 bg-pink-50 text-pink-700 dark:border-pink-900 dark:bg-pink-950 dark:text-pink-300",
  teal: "border-teal-200 bg-teal-50 text-teal-700 dark:border-teal-900 dark:bg-teal-950 dark:text-teal-300",
};
const COLORS = ["slate", "blue", "green", "amber", "red", "violet", "pink", "teal"];

export function tagColor(tag: string) {
  return COLORS[Array.from(tag).reduce((n, c) => n + c.charCodeAt(0), 0) % COLORS.length];
}

export function TagChip({ tag, onRemove, onClick, active }: { tag: string; onRemove?: () => void; onClick?: () => void; active?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                        TAG_TONE[tagColor(tag)], onClick && "cursor-pointer hover:opacity-80", active && "ring-2 ring-primary/40")}
          onClick={onClick}>
      #{tag}
      {onRemove && (
        <button type="button" aria-label={`Remove tag ${tag}`} onClick={(e) => { e.stopPropagation(); onRemove(); }} className="rounded-full hover:bg-black/10">
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

/** Tags of one profile, run or model: chips plus an inline add box with autocomplete. Loads its own tags when
 *  `initial` is not given. */
export function TagEditor({ entityType, entityKey, initial, canEdit = true, compact = false }: {
  entityType: TagType; entityKey: string; initial?: string[]; canEdit?: boolean; compact?: boolean;
}) {
  const [tags, setTags] = useState<string[]>(initial ?? []);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [known, setKnown] = useState<TagInfo[]>([]);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const input = useRef<HTMLInputElement>(null);
  const { can } = useAccess();
  canEdit = canEdit && can("TAG.MANAGE");

  useEffect(() => {
    if (initial !== undefined) { setTags(initial); return; }
    let live = true;
    loadEntityTags(entityType, entityKey).then((r) => { if (live && r.ok) setTags(r.data.tags); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityType, entityKey]);
  useEffect(() => {
    if (!adding) return;
    input.current?.focus();
    loadTags().then((r) => r.ok && setKnown(r.data.tags));
  }, [adding]);

  const save = (next: string[]) => start(async () => {
    setError("");
    const r = await saveEntityTags(entityType, entityKey, next);
    if (!r.ok) { setError(r.error); return; }
    setTags(r.data.tags);
  });
  const add = (raw: string) => {
    const tag = raw.trim().toLowerCase().replace(/^#/, "").replace(/\s+/g, "-");
    setText("");
    if (!tag || tags.includes(tag)) return;
    save([...tags, tag]);
  };
  const q = text.trim().toLowerCase().replace(/^#/, "");
  const options = known.filter((k) => !tags.includes(k.tag) && (!q || k.tag.includes(q))).slice(0, 6);

  return (
    <div className={cn("flex flex-wrap items-center gap-1", compact && "gap-0.5")} onClick={(e) => e.stopPropagation()}>
      {!compact && <Tag className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
      {tags.map((t) => <TagChip key={t} tag={t} onRemove={canEdit ? () => save(tags.filter((x) => x !== t)) : undefined} />)}
      {canEdit && (adding ? (
        <span className="relative">
          <input ref={input} value={text} onChange={(e) => setText(e.target.value)} aria-label="Add a tag"
                 onKeyDown={(e) => {
                   if (e.key === "Enter" || e.key === ",") { e.preventDefault(); if (text.trim()) add(text); }
                   if (e.key === "Escape") { setAdding(false); setText(""); }
                 }}
                 onBlur={() => setTimeout(() => { setAdding(false); setText(""); }, 150)}
                 placeholder="tag" className="h-6 w-28 rounded-full border bg-card px-2 text-[11px] outline-none focus:border-primary" />
          {options.length > 0 && (
            <span className="absolute left-0 top-7 z-40 w-44 rounded-lg border bg-card p-1 shadow-lg">
              {options.map((o) => (
                <button key={o.tag} type="button" onMouseDown={(e) => { e.preventDefault(); add(o.tag); }}
                        className="flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-muted">
                  <span>#{o.tag}</span><span className="text-[10px] text-muted-foreground">{o.count}</span>
                </button>
              ))}
            </span>
          )}
        </span>
      ) : (
        <button type="button" onClick={() => setAdding(true)} aria-label="Add a tag"
                className="inline-flex items-center gap-0.5 rounded-full border border-dashed px-1.5 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-primary">
          {pending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}{!compact && "tag"}
        </button>
      ))}
      {error && <span role="alert" className="text-[11px] text-destructive">{error}</span>}
    </div>
  );
}
