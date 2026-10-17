import Link from "next/link";
import { ArrowRight, TrendingUp } from "lucide-react";
import { Sparkline } from "@/app/dashboard/command-center";
import type { ValueSummary } from "@/lib/value-types";

const usd = (v: number | null | undefined) => (v == null ? "-" : `$${Math.round(v).toLocaleString()}`);

/** The value ledger at a glance (Command Center, My Work): hours saved, net value, automatic-fix share. */
export function ValueTile({ data }: { data: ValueSummary }) {
  const v = data.value;
  return (
    <Link href="/value" className="surface group relative flex flex-wrap items-center gap-x-6 gap-y-2 p-4 transition hover:border-primary/25" data-testid="value-tile">
      <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100"><TrendingUp className="h-4 w-4" /></span>
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">Value, last {data.window_days} days</p>
        <p className="text-lg font-semibold tabular-nums">{v.hours.toLocaleString()} hours saved
          <span className="ml-2 text-sm font-medium text-muted-foreground">{v.net_usd == null ? `${usd(v.value_usd)} of time` : `${usd(v.net_usd)} net of AI cost`}</span>
        </p>
      </div>
      <p className="text-sm text-muted-foreground">
        {data.auto_fix_share == null ? "No failures handled yet" : `${Math.round(data.auto_fix_share * 100)}% of failures fixed automatically`}
        {data.knowledge_score != null ? ` · knowledge score ${data.knowledge_score}` : ""}
      </p>
      <div className="ml-auto w-32"><Sparkline values={data.weekly_hours} /></div>
      <ArrowRight className="h-4 w-4 text-muted-foreground/60 transition group-hover:text-muted-foreground" />
    </Link>
  );
}
