"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { createPortal } from "react-dom";
import {
  ArrowRight, BarChart3, Boxes, ClipboardCheck, CornerDownLeft, GitCompareArrows, ListChecks, Loader2, Monitor, Moon, Search,
  SquareTerminal, Sun, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { setDomainScope } from "@/app/scope-actions";
import type { SearchHit } from "@/app/bff/search/route";
import { useTheme } from "@/components/theme/theme-provider";
import { allowedBy, NAV_GROUPS } from "./nav";
import { QUICK_ACTIONS } from "./quick-actions";
import type { DomainOption } from "./top-bar";

type Item = { id: string; group: string; label: string; detail?: string; icon: LucideIcon; keywords?: string; run: () => void };

const HIT_ICON: Record<SearchHit["kind"], LucideIcon> = {
  run: ListChecks, table: BarChart3, sttm: GitCompareArrows, qa: SquareTerminal, dq: ClipboardCheck,
};

function score(item: Item, q: string) {
  if (!q) return 1;
  const hay = `${item.label} ${item.detail ?? ""} ${item.keywords ?? ""}`.toLowerCase();
  const label = item.label.toLowerCase();
  if (label.startsWith(q)) return 3;
  if (label.includes(q)) return 2;
  return q.split(/\s+/).every((w) => hay.includes(w)) ? 1 : 0;
}

/** Ctrl/Cmd+K: jump to any page, start a quick action, switch domain or theme, or search runs, tables, STTM
 *  mappings, QA packs and DQ tables (/bff/search). Arrow keys move, Enter opens, Escape closes. */
export function CommandPalette({ privileges, domains }: { privileges?: string[] | null; domains: DomainOption[] }) {
  const router = useRouter();
  const { setChoice } = useTheme();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [, start] = useTransition();
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((v) => !v); }
    };
    const event = () => setOpen(true);
    window.addEventListener("keydown", key);
    window.addEventListener("aip:palette", event);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("aip:palette", event); };
  }, []);
  useEffect(() => {
    if (!open) { setQ(""); setHits([]); setActive(0); return; }
    document.documentElement.dataset.overlay = "open";
    return () => { delete document.documentElement.dataset.overlay; };
  }, [open]);

  // remote search, debounced
  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < 2) { setHits([]); setSearching(false); return; }
    setSearching(true);
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      fetch(`/bff/search?q=${encodeURIComponent(term)}`, { signal: ctrl.signal, cache: "no-store" })
        .then((r) => (r.ok ? r.json() : { hits: [] }))
        .then((d: { hits: SearchHit[] }) => setHits(d.hits ?? []))
        .catch(() => undefined)
        .finally(() => setSearching(false));
    }, 180);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [q, open]);

  const go = useCallback((href: string) => { setOpen(false); router.push(href); }, [router]);

  const items = useMemo(() => {
    const allowed = allowedBy(privileges);
    const base: Item[] = [
      ...QUICK_ACTIONS.filter((a) => allowed(a.privilege)).map((a) => ({
        id: `qa:${a.href}`, group: "Quick actions", label: a.label, detail: a.hint, icon: a.icon, run: () => go(a.href),
      })),
      ...NAV_GROUPS.flatMap((g) => g.links.filter((l) => allowed(l.privilege)).map((l) => ({
        id: `nav:${l.href}`, group: "Go to", label: l.label, detail: g.label, icon: l.icon, keywords: l.href, run: () => go(l.href),
      }))),
      ...[{ domain_id: "", domain_name: "" }, ...domains].map((d) => ({
        id: `dom:${d.domain_id || "all"}`, group: "Switch domain", label: d.domain_name || "All domains", icon: Boxes, keywords: "domain scope",
        run: () => { setOpen(false); start(async () => { await setDomainScope(d.domain_name); router.refresh(); }); },
      })),
      { id: "theme:light", group: "Theme", label: "Light theme", icon: Sun, keywords: "appearance", run: () => { setChoice("light"); setOpen(false); } },
      { id: "theme:dark", group: "Theme", label: "Dark theme", icon: Moon, keywords: "appearance night", run: () => { setChoice("dark"); setOpen(false); } },
      { id: "theme:system", group: "Theme", label: "Match system theme", icon: Monitor, keywords: "appearance auto", run: () => { setChoice("system"); setOpen(false); } },
    ];
    const term = q.trim().toLowerCase();
    const local = base.map((i) => ({ i, s: score(i, term) })).filter((x) => x.s > 0)
      // with no query: quick actions and pages only; domains and themes appear when typed for
      .filter((x) => term || x.i.group === "Quick actions" || x.i.group === "Go to")
      .sort((a, b) => (term ? b.s - a.s : 0)).map((x) => x.i).slice(0, term ? 12 : 40);
    const remote: Item[] = hits.map((h, n) => ({
      id: `hit:${n}:${h.href}`, group: "Results", label: h.title, detail: h.detail, icon: HIT_ICON[h.kind], run: () => go(h.href),
    }));
    return term ? [...remote, ...local] : local;
  }, [privileges, domains, q, hits, go, router, setChoice]);

  useEffect(() => { setActive(0); }, [q, hits]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); items[active]?.run(); }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
  };
  let lastGroup = "";
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 px-4 pt-[12vh] backdrop-blur-sm" onMouseDown={() => setOpen(false)}>
      <div role="dialog" aria-modal="true" aria-label="Command palette" data-testid="command-palette"
           className="popover w-full max-w-xl animate-pop-in overflow-hidden p-0 shadow-pop" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-border/70 px-4">
          {searching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : <Search className="h-4 w-4 text-muted-foreground" />}
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} role="combobox" aria-expanded="true"
                 aria-controls="palette-list" aria-activedescendant={items[active] ? `palette-${active}` : undefined}
                 placeholder="Search or jump to..." className="h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground/70" />
          <kbd className="kbd">Esc</kbd>
        </div>
        <div ref={list} id="palette-list" role="listbox" className="max-h-[min(60vh,440px)] overflow-y-auto p-1.5">
          {items.length === 0 && (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">{searching ? "Searching..." : `Nothing matches "${q}".`}</p>
          )}
          {items.map((item, n) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            const Icon = item.icon;
            return (
              <div key={item.id}>
                {header && <p className="px-2.5 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{header}</p>}
                <button type="button" id={`palette-${n}`} data-index={n} role="option" aria-selected={n === active}
                        onMouseMove={() => setActive(n)} onClick={() => item.run()}
                        className={cn("flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition",
                          n === active ? "bg-primary/10 text-foreground" : "text-foreground/90")}>
                  <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-md border border-border/70",
                    n === active ? "border-primary/30 bg-primary/10 text-primary" : "bg-muted/60 text-muted-foreground")}>
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{item.label}</span>
                    {item.detail && <span className="block truncate text-xs text-muted-foreground">{item.detail}</span>}
                  </span>
                  {n === active && <ArrowRight className="h-3.5 w-3.5 text-primary" />}
                </button>
              </div>
            );
          })}
        </div>
        <div className="flex items-center gap-4 border-t border-border/70 px-4 py-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> move</span>
          <span className="flex items-center gap-1"><kbd className="kbd"><CornerDownLeft className="h-3 w-3" /></kbd> open</span>
          <span className="ml-auto">Ctrl+J opens the copilot</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
