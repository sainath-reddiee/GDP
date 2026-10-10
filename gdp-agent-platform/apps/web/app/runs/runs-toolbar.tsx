"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const STAGES = [["SOURCE", "Source"], ["PROFILING", "Profiling"], ["DOMAIN", "Domain"], ["MAPPING", "Mapping"],
  ["STTM", "STTM"], ["SODA", "Data quality"], ["DBT", "dbt"], ["VALIDATION", "Validation"], ["REVIEW", "Code review"]];
const SORTS = [["newest", "Newest first"], ["updated", "Recently updated"], ["oldest", "Oldest first"], ["name", "Name"]];

/** Search, filters, sorting and paging for the run list; everything lives in the URL so views can be shared. */
export function RunsToolbar({ domains, tags = [], total, offset, limit }: {
  domains: { domain_id: string; domain_name: string }[]; tags?: string[]; total: number; offset: number; limit: number;
}) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const urlQ = params.get("q") ?? "";
  useEffect(() => { setQ(urlQ); }, [urlQ]); // back/forward or a link changed the search

  const go = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!("offset" in changes)) next.delete("offset");
    router.push(`${path}?${next.toString()}`);
  };
  const select = "h-8 rounded-md border bg-card px-2 text-xs";
  const review = params.get("needs_review") === "1";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <form onSubmit={(e) => { e.preventDefault(); go({ q: q.trim() || null }); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search runs"
               placeholder="Search name, source, target, domain or owner"
               className="h-8 w-72 rounded-md border bg-card px-3 text-xs" />
      </form>
      <select aria-label="Domain" className={select} value={params.get("domain_id") ?? ""} onChange={(e) => go({ domain_id: e.target.value || null })}>
        <option value="">All domains</option>
        {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
      </select>
      <select aria-label="Stage" className={select} value={params.get("stage") ?? ""} onChange={(e) => go({ stage: e.target.value || null })}>
        <option value="">Any stage</option>
        {STAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      {tags.length > 0 && (
        <select aria-label="Tag" className={select} value={params.get("tag") ?? ""} onChange={(e) => go({ tag: e.target.value || null })}>
          <option value="">Any tag</option>
          {tags.map((t) => <option key={t} value={t}>#{t}</option>)}
        </select>
      )}
      <button type="button" onClick={() => go({ needs_review: review ? null : "1" })} aria-pressed={review}
              className={cn("h-8 rounded-md border px-2.5 text-xs font-medium",
                review ? "border-rose-500/40 bg-rose-500/10 text-rose-600" : "hover:bg-muted")}>
        Needs review
      </button>
      <select aria-label="Sort" className={select} value={params.get("sort") ?? "newest"} onChange={(e) => go({ sort: e.target.value === "newest" ? null : e.target.value })}>
        {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <div className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
        <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}</span>
        <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => go({ offset: String(Math.max(0, offset - limit)) })}>Previous</Button>
        <Button size="sm" variant="ghost" disabled={offset + limit >= total} onClick={() => go({ offset: String(offset + limit) })}>Next</Button>
      </div>
    </div>
  );
}
