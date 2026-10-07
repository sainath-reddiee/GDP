import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { ReconcileButton } from "@/app/audit/reconcile-button";
import type { PlatformState, ReconcileResult } from "./actions";
import { RateCardEditor } from "./rate-card";
import { Panel, Stat } from "./section";

type Row = { key: string | null; calls: number; total_tokens: number; credits?: number; estimated_cost?: number;
  actual_credits?: number | null; estimated_credits?: number };
type Costs = { rows: Row[]; totals: Record<string, number>; credit_price_usd?: number | null; reconcile?: ReconcileResult | null };

const n = (v: unknown) => Number(v ?? 0);
const credits = (r: Row) => n(r.credits ?? r.estimated_cost);
const empty: Costs = { rows: [], totals: {} };

function days(rows: Row[]): { day: string; actual: number; estimated: number }[] {
  const byDay = new Map(rows.map((r) => [r.key ?? "", r]));
  const out = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86_400_000).toISOString().slice(0, 10);
    const r = byDay.get(d);
    out.push({ day: d, actual: n(r?.actual_credits), estimated: n(r?.estimated_credits ?? (r ? credits(r) - n(r.actual_credits) : 0)) });
  }
  return out;
}

function Breakdown({ rows, total }: { rows: Row[]; total: number }) {
  if (!rows.length) return <p className="text-sm text-muted-foreground">No AI usage in the last 30 days.</p>;
  return (
    <ul className="space-y-2.5">
      {rows.slice(0, 8).map((r) => {
        const value = credits(r);
        return (
          <li key={r.key ?? "none"}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate">{r.key ? r.key.toLowerCase() : "not linked"}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{value.toFixed(3)} · {n(r.calls).toLocaleString()} calls</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${total ? Math.max(2, (value / total) * 100) : 0}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** 30-day AI cost (billed where Snowflake has reported it, estimated otherwise), the rate card and reconcile. */
export async function CostSection({ platform, modelNames }: { platform: PlatformState | null; modelNames: string[] }) {
  const since = new Date(Date.now() - 29 * 86_400_000).toISOString().slice(0, 10);
  const get = (g: string) => api<Costs>(`/api/costs?group_by=${g}&since=${since}&limit=200`).catch(() => empty);
  const [byDay, byStage, byModel] = await Promise.all([get("day"), get("stage"), get("model")]);
  const t = byStage.totals;
  const total = n(t.credits ?? t.estimated_cost);
  const price = byStage.credit_price_usd ?? null;
  const series = days(byDay.rows);
  const peak = Math.max(0.000001, ...series.map((d) => d.actual + d.estimated));
  const s = platform?.settings ?? {};

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Credits, last 30 days" value={total.toFixed(3)} hint={price ? `about $${(total * price).toFixed(2)}` : "Set a credit price to see dollars"} />
        <Stat label="Billed by Snowflake" value={n(t.actual_credits).toFixed(3)} hint={`${n(t.actual_calls).toLocaleString()} calls matched`} />
        <Stat label="Estimated, not billed yet" value={n(t.estimated_credits).toFixed(3)} hint="Replaced by billed credits on reconcile" />
        <Stat label="AI calls" value={n(t.calls).toLocaleString()} hint={`${n(t.total_tokens).toLocaleString()} tokens`} />
      </div>

      <Panel title="Daily credits" description="Billed credits come from Snowflake's Cortex usage views, matched by query id. They lag by a few hours, so recent days show estimates first."
             actions={<Link href="/audit?tab=cost" className="flex items-center gap-1 text-xs text-primary hover:underline">Full breakdown <ArrowRight className="h-3 w-3" /></Link>}>
        <div className="flex h-40 items-end gap-[3px]" role="img" aria-label="Credits per day for the last 30 days">
          {series.map((d) => (
            <div key={d.day} className="group relative flex h-full flex-1 flex-col justify-end" title={`${d.day}: ${d.actual.toFixed(3)} billed, ${d.estimated.toFixed(3)} estimated`}>
              <div className="w-full rounded-t-sm bg-primary/35" style={{ height: `${(d.estimated / peak) * 100}%` }} />
              <div className="w-full bg-primary" style={{ height: `${(d.actual / peak) * 100}%` }} />
            </div>
          ))}
        </div>
        <div className="mt-2 flex justify-between text-[10px] text-muted-foreground">
          <span>{series[0].day.slice(5)}</span><span>{series[series.length - 1].day.slice(5)}</span>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-4 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-primary" /> Billed</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-primary/35" /> Estimated</span>
          </div>
          <ReconcileButton last={byStage.reconcile} />
        </div>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="By stage"><Breakdown rows={byStage.rows} total={total} /></Panel>
        <Panel title="By model"><Breakdown rows={byModel.rows} total={total} /></Panel>
      </div>

      <RateCardEditor
        rateCard={(s.RATE_CARD?.value as Record<string, { input?: number; output?: number }>) ?? {}}
        fallback={n((s.CREDITS_PER_MILLION_TOKENS?.value as Record<string, number> | undefined)?.default)}
        legacy={(s.CREDITS_PER_MILLION_TOKENS?.value as Record<string, number>) ?? {}}
        price={(s.CREDIT_PRICE_USD?.value as number | null) ?? null}
        modelNames={Array.from(new Set([...modelNames, ...byModel.rows.map((r) => r.key ?? "").filter((k) => k && k !== "unknown")]))}
      />
    </div>
  );
}
