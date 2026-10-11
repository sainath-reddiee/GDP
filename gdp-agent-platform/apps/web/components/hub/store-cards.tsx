import { BarChart3, ClipboardCheck, Database, Eye, GitBranchPlus, GitCompareArrows, Network, PlugZap, Plus, SquareTerminal, TrendingUp } from "lucide-react";
import type { Overview } from "@/lib/store-types";
import { fmt, StoreCard } from "@/app/dashboard/command-center";

const pct = (part: number, whole: number) => (whole ? (100 * part) / whole : null);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("sh") ? "es" : "s"}`;

/** The six asset stores as cards (what each holds, what needs action, one task to start), from /api/hub/overview.
 * Shown on the Command Center and the Store hub; `domain` (an explicit ?domain=) is carried onto every link. */
export function StoreCards({ o, domain }: { o: Overview | null; domain?: string }) {
  const idle = o?.sources ? o.sources.external - o.sources.external_active : 0;
  const h = (href: string) => (domain ? `${href}${href.includes("?") ? "&" : "?"}domain=${encodeURIComponent(domain)}` : href);
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StoreCard testId="store-source" title="Source Store" subtitle="Shares, databases and external pipelines" icon={Database} tone="indigo" missing={!o?.sources}
                     metrics={[{ label: "Shares", value: fmt(o?.sources?.shares) }, { label: "Databases", value: fmt(o?.sources?.databases) },
                               { label: "Pipelines", value: fmt(o?.sources?.external) }]}
                     href={h("/dashboard/sources")} action={{ label: "Onboard source", href: "/dashboard/onboarding?type=source", icon: PlugZap }}
                     alert={idle ? { text: `${plural(idle, "external pipeline")} not active yet`, tone: "amber" } : null} />
          <StoreCard testId="store-profiling" title="Profiling Store" subtitle="Distributions, nulls, cardinality and drift" icon={BarChart3} tone="sky" missing={!o?.profiling}
                     metrics={[{ label: "Tables profiled", value: fmt(o?.profiling?.tables) },
                               { label: "Drift alerts", value: fmt(o?.profiling?.drift), tone: o?.profiling?.drift ? "text-amber-600" : undefined },
                               { label: "High cardinality", value: fmt(o?.profiling?.high_cardinality) }]}
                     meter={{ label: "Profiling health", value: o?.profiling?.health }}
                     href={h("/dashboard/profiling")} action={{ label: "Review drift", href: "/dashboard/profiling?sort=drift", icon: TrendingUp }}
                     alert={o?.profiling?.drift_tables ? { text: `${plural(o.profiling.drift_tables, "table")} drifted since the last pass`, tone: "amber" } : null} />
          <StoreCard testId="store-models" title="Modeling Store" subtitle="Domain models published to feature branches" icon={Network} tone="violet" missing={!o?.models}
                     metrics={[{ label: "Published", value: fmt(o?.models?.published) }, { label: "Branches", value: fmt(o?.models?.branches) },
                               { label: "In design", value: fmt(o?.models?.in_design) }]}
                     href={h("/dashboard/modeling")} action={{ label: "New model", href: "/dashboard/modeling", icon: GitBranchPlus }}
                     alert={o?.models?.failed ? { text: `${plural(o.models.failed, "publish")} failed`, tone: "rose" }
                       : o?.models?.in_design ? { text: `${plural(o.models.in_design, "design")} in progress`, tone: "violet" } : null} />
          <StoreCard testId="store-sttm" title="STTM Store" subtitle="Versioned source to target mappings" icon={GitCompareArrows} tone="emerald" missing={!o?.sttm}
                     metrics={[{ label: "Verified", value: fmt(o?.sttm?.verified), tone: "text-emerald-600" }, { label: "Pending review", value: fmt(o?.sttm?.pending) },
                               { label: "Mappings", value: fmt(o?.sttm?.mappings) }]}
                     meter={{ label: "Verified share", value: o?.sttm ? pct(o.sttm.verified, o.sttm.mappings) : null, tone: "primary" }}
                     href={h("/dashboard/sttm")} action={{ label: "Review pending", href: "/dashboard/sttm?status=REVIEW", icon: Eye }}
                     alert={o?.sttm?.pending ? { text: `${plural(o.sttm.pending, "mapping")} waiting for review`, tone: "amber" } : null} />
          <StoreCard testId="store-qa" title="QA Store" subtitle={o?.qa?.top_tags?.length ? o.qa.top_tags.slice(0, 3).map((t) => `#${t}`).join(" ") : "Deterministic domain SQL packs"}
                     icon={SquareTerminal} tone="amber" missing={!o?.qa}
                     metrics={[{ label: "Domain packs", value: fmt(o?.qa?.approved_packs) }, { label: "Run tests", value: fmt(o?.qa?.run_tests) },
                               { label: "Pass rate", value: o?.qa?.pass_rate == null ? "-" : `${Math.round(o.qa.pass_rate)}%` }]}
                     href={h("/dashboard/qa-store")} action={{ label: "New pack", href: "/dashboard/qa-store?new=1", icon: Plus }}
                     alert={o?.qa?.pending ? { text: `${plural(o.qa.pending, "pack")} awaiting approval`, tone: "violet" } : null} />
          <StoreCard testId="store-dq" title="Data Quality Store"
                     subtitle={o?.dq ? `${fmt(o.dq.run_checks)} approved in runs · ${fmt(o.dq.store_checks)} in the store` : "Soda and Great Expectations attribute checks"} icon={ClipboardCheck} tone="rose" missing={!o?.dq}
                     metrics={[{ label: "Active checks", value: fmt(o?.dq?.checks) },
                               { label: "Failing", value: fmt(o?.dq?.failed), tone: o?.dq?.failed ? "text-rose-600" : undefined },
                               { label: "Scheduled tables", value: fmt(o?.dq?.scheduled_tables) }]}
                     meter={{ label: "Coverage of profiled tables", value: o?.dq?.coverage, tone: "primary" }}
                     href={h("/dashboard/dq-store")} action={{ label: "Add check", href: "/dashboard/dq-store?add=1", icon: Plus }}
                     alert={o?.dq?.failed ? { text: `${plural(o.dq.failed, "check")} failing`, tone: "rose" }
                       : o?.dq?.never_run ? { text: `${plural(o.dq.never_run, "check")} never run`, tone: "amber" } : null} />
        </div>
  );
}
