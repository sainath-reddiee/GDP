import Link from "next/link";
import { Crown, Database } from "lucide-react";
import { PIPELINES, type Pipeline } from "@/lib/stages";
import { cn } from "@/lib/utils";

const ICON: Record<Pipeline, typeof Database> = { SILVER: Database, GOLD: Crown };

/** The two pipelines on one engine: 1, the Silver Pipeline (source to Silver, pre-MDM); 2, the Gold Pipeline (Silver,
 *  read in place, to Gold). Same steps and gates in both. */
export function PipelineTabs({ active }: { active: Pipeline }) {
  return (
    <nav aria-label="Pipelines" className="grid gap-2 sm:grid-cols-2">
      {(["SILVER", "GOLD"] as const).map((id) => {
        const p = PIPELINES[id];
        const Icon = ICON[id];
        const on = active === id;
        return (
          <Link key={id} href={p.href} aria-current={on ? "page" : undefined}
                className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 transition",
                  on ? "border-primary/50 bg-primary/5 ring-1 ring-primary/20" : "bg-card hover:bg-muted")}>
            <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
              <Icon className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{p.n}. {p.name}
                <span className="ml-2 text-xs font-normal text-muted-foreground">{p.from} to {p.to}</span></span>
              <span className="block text-xs text-muted-foreground">{p.hint}</span>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
