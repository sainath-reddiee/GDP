"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { Loader2, Search, X } from "lucide-react";

/** The boards' request search: filters as you type (the page matches ?q= on the server, by meaning through GET /api/search). */
export function HomeSearch({ placeholder = "Search requests by meaning" }: { placeholder?: string }) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [pending, start] = useTransition();
  const first = useRef(true);

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const t = setTimeout(() => {
      const next = new URLSearchParams(params.toString());
      if (q.trim()) next.set("q", q.trim()); else next.delete("q");
      const query = next.toString();
      start(() => router.replace(query ? `${path}?${query}` : path, { scroll: false }));
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  return (
    <label className="relative block w-full sm:w-[280px]">
      <span className="sr-only">{placeholder}</span>
      {pending ? <Loader2 className="os-faint os-spin absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
        : <Search className="os-faint absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />}
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} data-testid="home-search"
             className="os-input h-9 !py-0 !pl-8 !pr-8" />
      {q && (
        <button type="button" onClick={() => setQ("")} aria-label="Clear search" className="os-faint absolute right-2.5 top-1/2 -translate-y-1/2 hover:text-[var(--os-ink)]">
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </label>
  );
}
