"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { BookOpen, Boxes, Database, GitBranch, Loader2, Search, Sparkles, Table2, Workflow, X, type LucideIcon } from "lucide-react";
import { searchPlatform } from "@/app/os/search-actions";
import { KIND_LABEL, KIND_ORDER, MODE_LABEL, type SearchKind, type SearchResponse, type SearchResult } from "@/lib/os/search";
import { cn } from "@/lib/utils";

// The Home search bar: by meaning across workflows, domains, sources, runs, tables and knowledge (GET /api/search).
// Typing filters the workflows below (?q=, matched on the server by the same search) and opens the results across
// every kind; arrows move, Enter opens, Escape closes, "/" focuses it from anywhere on the page.

const ICON: Record<SearchKind, LucideIcon> = {
  WORKFLOW: Workflow, DOMAIN: Boxes, SOURCE: Database, RUN: GitBranch, TABLE: Table2, KNOWLEDGE: BookOpen,
};
const TRY = ["waiting on me", "failed runs", "client accounts", "property tables", "what is net rentable area"];

export function PlatformSearch({ placeholder = "Search by meaning: workflows, domains, sources, runs, tables, knowledge" }: { placeholder?: string }) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [routing, startRouting] = useTransition();
  const [searching, setSearching] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  const asked = useRef(0);

  // the workflows below follow ?q=
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const t = setTimeout(() => {
      const next = new URLSearchParams(params.toString());
      if (q.trim()) next.set("q", q.trim()); else next.delete("q");
      const query = next.toString();
      startRouting(() => router.replace(query ? `${path}?${query}` : path, { scroll: false }));
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // the results across every kind
  useEffect(() => {
    if (!q.trim()) { setFound(null); setError(null); return; }
    const n = ++asked.current;
    const t = setTimeout(async () => {
      setSearching(true);
      const r = await searchPlatform(q.trim());
      if (n !== asked.current) return;
      setSearching(false);
      if (r.ok) { setFound(r.data); setError(null); setActive(0); } else { setError(r.error); setFound(null); }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  // "/" focuses the search; a click elsewhere closes the results
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !(el && (el.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(el.tagName)))) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    const click = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", key);
    window.addEventListener("mousedown", click);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("mousedown", click); };
  }, []);

  const groups = useMemo(() => {
    const rows = found?.results ?? [];
    return KIND_ORDER.map((k) => ({ kind: k, rows: rows.filter((r) => r.kind === k) })).filter((g) => g.rows.length);
  }, [found]);
  const flat = groups.flatMap((g) => g.rows);

  const go = (r: SearchResult) => { setOpen(false); router.push(r.href); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); input.current?.blur(); return; }
    if (!flat.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => (a + 1) % flat.length); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + flat.length) % flat.length); }
    if (e.key === "Enter" && open && flat[active]) { e.preventDefault(); go(flat[active]); }
  };
  const busy = routing || searching;

  return (
    <div ref={box} className="relative w-full" data-testid="platform-search">
      <label className="relative block">
        <span className="sr-only">Search the platform</span>
        {busy ? <Loader2 className="os-faint os-spin absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" />
          : <Search className="os-faint absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" />}
        <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onKeyDown={onKey}
               placeholder={placeholder} data-testid="home-search" role="combobox" aria-expanded={open} aria-controls="platform-search-results"
               aria-autocomplete="list" aria-activedescendant={open && flat[active] ? `ps-${flat[active].kind}-${flat[active].id}` : undefined}
               className="os-input h-10 w-full !py-0 !pl-10 !pr-16 text-[13px]" />
        {q ? (
          <button type="button" onClick={() => { setQ(""); input.current?.focus(); }} aria-label="Clear search"
                  className="os-faint absolute right-3 top-1/2 -translate-y-1/2 hover:text-[var(--os-ink)]"><X className="h-4 w-4" /></button>
        ) : (
          <kbd className="os-faint pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border border-[var(--os-line)] px-1.5 text-[10.5px] font-bold sm:block">/</kbd>
        )}
      </label>

      {open && (
        <div id="platform-search-results" role="listbox" data-testid="platform-search-results"
             className="os-card absolute inset-x-0 top-[calc(100%+6px)] z-50 max-h-[min(70vh,560px)] overflow-y-auto p-2 shadow-xl">
          {!q.trim() ? (
            <div className="p-2">
              <div className="os-faint mb-2 text-[11px] font-bold">Ask in plain words, or try</div>
              <div className="flex flex-wrap gap-1.5">
                {TRY.map((t) => (
                  <button key={t} type="button" className="os-tag os-tag-blue hover:opacity-80" onClick={() => { setQ(t); input.current?.focus(); }}>
                    <Sparkles className="h-3 w-3" />{t}
                  </button>
                ))}
              </div>
            </div>
          ) : error ? (
            <p role="alert" className="p-3 text-[12px] text-[var(--os-red-ink)]">Search is not available: {error}</p>
          ) : !found ? (
            <p className="os-faint p-3 text-[12px]">Searching...</p>
          ) : !flat.length ? (
            <p className="os-faint p-3 text-[12px]">Nothing matches &quot;{q.trim()}&quot;{found.intent.mine ? " that waits on you" : ""}. Try other words; the search understands synonyms such as client for customer.</p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2 px-2 pb-1.5 pt-0.5 text-[10.5px]">
                <span className="os-faint font-bold">{flat.length} result{flat.length === 1 ? "" : "s"}{found.intent.mine ? " waiting on you" : ""}{found.intent.failed ? " that need attention" : ""}</span>
                {MODE_LABEL[found.mode] && <span className="os-tag" title={found.mode === "keyword" ? "The semantic search service is not deployed; names, synonyms and stems are matched" : "Cortex Search by meaning, fused with name matching"}>{MODE_LABEL[found.mode]}</span>}
              </div>
              {groups.map((g) => (
                <div key={g.kind} className="mb-1">
                  <div className="os-eyebrow px-2 pb-1 pt-1.5">{KIND_LABEL[g.kind]}</div>
                  {g.rows.map((r) => {
                    const I = ICON[r.kind];
                    const i = flat.indexOf(r);
                    return (
                      <Link key={`${r.kind}-${r.id}`} id={`ps-${r.kind}-${r.id}`} href={r.href} role="option" aria-selected={i === active}
                            onMouseEnter={() => setActive(i)} onClick={() => setOpen(false)}
                            className={cn("flex items-start gap-2.5 rounded-lg px-2 py-1.5", i === active && "bg-[var(--os-hover)]")}>
                        <I className="os-faint mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[12.5px] font-bold">{r.title}</span>
                          {(r.subtitle || r.domain_name) && <span className="os-faint block truncate text-[11px]">{[r.subtitle, r.kind !== "DOMAIN" ? r.domain_name : null].filter(Boolean).join(" · ")}</span>}
                        </span>
                        <span className="hidden shrink-0 flex-wrap justify-end gap-1 sm:flex">
                          {r.why.filter((w) => w !== "filter").slice(0, 2).map((w) => <span key={w} className="os-tag text-[10px]">{w}</span>)}
                        </span>
                      </Link>
                    );
                  })}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
