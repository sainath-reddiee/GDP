import Link from "next/link";
import { Store } from "lucide-react";
import { cn } from "@/lib/utils";
import { STORES } from "@/components/shell/nav";

/** Where a store tab leads, keeping an explicit ?domain= (the domain picked in the top bar applies everywhere anyway). */
export function storeHref(href: string, domain?: string | null) {
  return domain ? `${href}?domain=${encodeURIComponent(domain)}` : href;
}

/** One bar across the six asset stores, under each store's header and on the Store hub. Plain links to each store's own
 * route, so the browser history, bookmarks and the back button follow the tab you are on. */
export function StoreNav({ current, domain }: { current: string | null; domain?: string | null }) {
  return (
    <nav aria-label="Stores" data-testid="store-nav"
         className="-mx-1 flex items-center gap-1 overflow-x-auto rounded-xl border bg-card/60 p-1 text-sm shadow-sm">
      <Link href={storeHref("/dashboard/store", domain)} aria-current={current === null ? "page" : undefined}
            className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition",
                          current === null ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
        <Store className="h-4 w-4" />All stores
      </Link>
      <span className="mx-1 h-5 w-px shrink-0 bg-border" aria-hidden />
      {STORES.map((s) => {
        const on = s.id === current;
        const Icon = s.icon;
        return (
          <Link key={s.id} href={storeHref(s.href, domain)} aria-current={on ? "page" : undefined} data-testid={`store-nav-${s.id}`}
                className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 transition",
                              on ? "bg-primary/10 font-medium text-primary ring-1 ring-inset ring-primary/20" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
            <Icon className="h-4 w-4" />{s.short}
          </Link>
        );
      })}
    </nav>
  );
}

/** Store › {store} › {item} above a store's detail page. */
export function StoreCrumb({ store, trail = [] }: { store: string; trail?: { label: string; href?: string }[] }) {
  const s = STORES.find((x) => x.id === store);
  const parts = [{ label: "Store", href: "/dashboard/store" }, ...(s ? [{ label: s.label, href: s.href }] : []), ...trail];
  return (
    <nav aria-label="Breadcrumb" className="eyebrow">
      <ol className="flex flex-wrap items-center gap-1.5">
        {parts.map((p, i) => (
          <li key={`${p.label}-${i}`} className="flex items-center gap-1.5">
            {i > 0 && <span aria-hidden className="text-muted-foreground/60">›</span>}
            {p.href && i < parts.length - 1 ? <Link href={p.href} className="hover:underline">{p.label}</Link>
              : <span aria-current={i === parts.length - 1 ? "page" : undefined}>{p.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}
