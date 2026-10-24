import { BarChart3, ClipboardCheck, Database, Eye, GitCompareArrows, Network, PlugZap, Plus, SquareTerminal, TableProperties, TrendingUp } from "lucide-react";
import type { Overview } from "@/lib/store-types";
import { fmt, StoreCard } from "@/app/dashboard/command-center";
import { CountUp } from "@/components/motion/count-up";

const pct = (part: number, whole: number) => (whole ? (100 * part) / whole : null);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("sh") ? "es" : "s"}`;

/** The six asset stores as cards (what each holds, what needs action, one task to start), from /api/hub/overview.
 * Shown on the Command Center and the Store hub; `domain` (an explicit ?domain=) is carried onto every link. */
export function StoreCards({ o, domain, onboard = false }: { o: Overview | null; domain?: string; onboard?: boolean }) {
  const idle = o?.sources ? o.sources.external - o.sources.external_active : 0;
  const h = (href: string) => (domain ? `${href}${href.includes("?") ? "&" : "?"}domain=${encodeURIComponent(domain)}` : href);
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StoreCard testId="store-source" title="Source Store" subtitle="Shares, databases, files and external pipelines" icon={Database} tone="indigo" missing={!o?.sources}
                     metrics={[{ label: "Shares", value: <CountUp value={o?.sources?.shares} /> }, { label: "Databases", value: <CountUp value={o?.sources?.databases} /> },
                               { label: "Files, other", value: <CountUp value={o?.sources?.other} /> }, { label: "Pipelines", value: <CountUp value={o?.sources?.external} /> }]}
                     href={h("/os/design/sources")} action={onboard ? { label: "Onboard source", href: "/os/enable/onboarding?type=source", icon: PlugZap } : { label: "Request a source", href: "/os/intake/new", icon: PlugZap }}
                     alert={idle ? { text: `${plural(idle, "external pipeline")} not active yet`, tone: "amber" } : null} />
          <StoreCard testId="store-profiling" title="Profiling Store" subtitle="Distributions, nulls, cardinality and drift" icon={BarChart3} tone="sky" missing={!o?.profiling}
                     metrics={[{ label: "Tables profiled", value: <CountUp value={o?.profiling?.tables} /> },
                               { label: "Drift alerts", value: <CountUp value={o?.profiling?.drift} />, tone: o?.profiling?.drift ? "text-amber-600" : undefined },
                               { label: "High cardinality", value: <CountUp value={o?.profiling?.high_cardinality} /> }]}
                     meter={{ label: "Profiling health", value: o?.profiling?.health }}
                     href={h("/os/design/profiling")} action={{ label: "Review drift", href: "/os/design/profiling?sort=drift", icon: TrendingUp }}
                     alert={o?.profiling?.drift_tables ? { text: `${plural(o.profiling.drift_tables, "table")} drifted since the last pass`, tone: "amber" } : null} />
          <StoreCard testId="store-models" title="Modeling Store" subtitle="Domain and table models, versioned and reviewed" icon={Network} tone="violet" missing={!o?.models}
                     metrics={[{ label: "Domain models", value: <CountUp value={o?.models?.approved} /> }, { label: "Table models", value: <CountUp value={o?.models?.table_models} /> },
                               { label: "Open PRs", value: <CountUp value={o?.models?.prs_open} /> }]}
                     href={h("/os/design/modeling")} action={{ label: "New table model", href: "/os/design/modeling?view=tables&new=1", icon: TableProperties }}
                     alert={o?.models?.failed ? { text: `${plural(o.models.failed, "publish")} failed`, tone: "rose" }
                       : o?.models?.in_review ? { text: `${plural(o.models.in_review, "domain model")} in review`, tone: "violet" }
                       : o?.models?.drafts ? { text: `${plural(o.models.drafts, "domain model draft")} open`, tone: "violet" } : null}
                     note={o?.models ? [`${fmt(o.models.tables_published)} table model${o.models.tables_published === 1 ? "" : "s"} published`,
                                        o.models.legacy_designs ? `${fmt(o.models.legacy_designs)} legacy design${o.models.legacy_designs === 1 ? "" : "s"}` : ""]
                                        .filter(Boolean).join(" · ") : undefined} />
          <StoreCard testId="store-sttm" title="Data Mapping Store" subtitle="Versioned source to target mappings" icon={GitCompareArrows} tone="emerald" missing={!o?.sttm}
                     metrics={[{ label: "Approved", value: <CountUp value={o?.sttm?.approved} />, tone: "text-emerald-600" }, { label: "In review", value: <CountUp value={o?.sttm?.pending} /> },
                               { label: "Mappings", value: <CountUp value={o?.sttm?.mappings} /> }]}
                     meter={{ label: "Approved share", value: o?.sttm ? pct(o.sttm.approved, o.sttm.mappings) : null, tone: "primary" }}
                     href={h("/os/design/sttm")} action={{ label: "Review pending", href: "/os/design/sttm?status=REVIEW", icon: Eye }}
                     alert={o?.sttm?.pending ? { text: `${plural(o.sttm.pending, "mapping")} waiting for review`, tone: "amber" } : null} />
          <StoreCard testId="store-qa" title="QA Store" subtitle={o?.qa?.top_tags?.length ? o.qa.top_tags.slice(0, 3).map((t) => `#${t}`).join(" ") : "Deterministic domain SQL packs"}
                     icon={SquareTerminal} tone="amber" missing={!o?.qa}
                     metrics={[{ label: "Domain packs", value: <CountUp value={o?.qa?.approved_packs} /> }, { label: "Saved tests", value: <CountUp value={o?.qa?.saved_tests} /> },
                               { label: "Tests run (7 days)", value: <CountUp value={o?.qa?.tests_7d} /> }]}
                     meter={{ label: "Test pass rate (latest result, passed of executed)", value: o?.qa?.test_pass_rate }}
                     href={h("/os/design/qa-store")} action={{ label: "New QA pack", href: "/os/design/qa-store?new=1", icon: Plus }}
                     alert={o?.qa?.pending ? { text: `${plural(o.qa.pending, "pack")} awaiting approval`, tone: "violet" } : null} />
          <StoreCard testId="store-dq" title="Data Quality Store"
                     subtitle="Soda checks approved in runs and kept in the store" icon={ClipboardCheck} tone="rose" missing={!o?.dq}
                     metrics={[{ label: "Run checks", value: <CountUp value={o?.dq?.run_checks} /> }, { label: "Store checks", value: <CountUp value={o?.dq?.store_checks} /> },
                               { label: "Failing", value: <CountUp value={o?.dq?.failed} />, tone: o?.dq?.failed ? "text-rose-600" : undefined }]}
                     meter={{ label: o?.dq?.targets ? `Target tables with checks (${fmt(o.dq.covered_targets)} of ${fmt(o.dq.targets)})` : "Target tables with checks",
                              value: o?.dq?.coverage, tone: "primary" }}
                     href={h("/os/design/dq-store")} action={{ label: "Add a check", href: "/os/design/dq-store?add=1", icon: Plus }}
                     alert={o?.dq?.failed ? { text: `${plural(o.dq.failed, "check")} failing`, tone: "rose" }
                       : o?.dq?.never_run ? { text: `${plural(o.dq.never_run, "check")} never run`, tone: "amber" } : null} />
        </div>
  );
}
