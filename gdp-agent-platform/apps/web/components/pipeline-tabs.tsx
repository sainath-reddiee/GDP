import Link from "next/link";
import { Crown, Database } from "lucide-react";
import { cn } from "@/lib/utils";

/** The two pipelines: 1, Source to Silver (pre-MDM) runs; 2, Silver (post-MDM) to Gold builds. */
export function PipelineTabs({ active }: { active: "silver" | "gold" }) {
  const tabs = [
    { id: "silver", href: "/runs", label: "Source to Silver", hint: "Pipeline 1: ingest, profile, model, Silver code (pre-MDM)", icon: Database },
    { id: "gold", href: "/gold", label: "Silver to Gold", hint: "Pipeline 2: mastered data to facts, dimensions and bridges", icon: Crown },
  ] as const;
  return (
    <nav aria-label="Pipelines" className="grid gap-2 sm:grid-cols-2">
      {tabs.map((t, i) => (
        <Link key={t.id} href={t.href} aria-current={active === t.id ? "page" : undefined}
              className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 transition",
                active === t.id ? "border-primary/50 bg-primary/5 ring-1 ring-primary/20" : "bg-card hover:bg-muted")}>
          <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", active === t.id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
            <t.icon className="h-4 w-4" />
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold">{i + 1}. {t.label}</span>
            <span className="block text-xs text-muted-foreground">{t.hint}</span>
          </span>
        </Link>
      ))}
    </nav>
  );
}
