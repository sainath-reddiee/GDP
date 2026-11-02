"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** A segmented control of links: Home's views and filters live in the URL. A page reads the platform on every switch,
 *  so the tab you click takes the highlight at once and shows a spinner until the new page arrives. */
export function Segmented({ label, options, testId }: {
  label: string; testId?: string;
  options: { key: string; label: string; href: string; active: boolean; count?: number; dot?: boolean; disabled?: boolean; title?: string }[];
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [target, setTarget] = useState<string | null>(null);
  const go = (e: React.MouseEvent<HTMLAnchorElement>, key: string, href: string) => {
    // let the browser handle new-tab and modified clicks
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    setTarget(key);
    start(() => router.push(href, { scroll: false }));
  };
  return (
    <nav aria-label={label} aria-busy={pending || undefined} data-testid={testId}
         className="inline-flex max-w-full overflow-x-auto rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] py-1 pl-2 pr-1 gap-0.5">
      {options.map((o) => {
        if (o.disabled) {
          return (
            <span key={o.key} aria-disabled="true" title={o.title} data-testid={`${testId ?? "seg"}-${o.key}`}
                  className="os-faint inline-flex cursor-not-allowed items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-bold opacity-60">{o.label}</span>
          );
        }
        const loading = pending && target === o.key;
        const active = pending && target ? target === o.key : o.active;
        return (
          <Link key={o.key} href={o.href} scroll={false} onClick={(e) => go(e, o.key, o.href)} aria-current={o.active ? "page" : undefined} title={o.title}
                data-testid={testId ? `${testId}-${o.key}` : undefined}
                className={cn("relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-bold transition",
                              active ? "bg-[var(--os-forest)] text-white shadow-[0_2px_8px_rgba(0,63,45,0.3)]" : "os-muted hover:bg-[var(--os-surface)] hover:text-[var(--os-ink)]")}>
            {loading ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : o.dot && <span className="h-1.5 w-1.5 rounded-full bg-[#d85b52]" aria-hidden />}
            {o.label}
            {o.count !== undefined && (
              <span className={cn("rounded-full px-1.5 text-[10.5px] tabular-nums", active ? "bg-white/20 text-white" : "bg-[var(--os-neutral-bg)]")}>{o.count}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
