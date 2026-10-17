"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Clock, Coins, Gauge, Loader2, Pencil, RotateCcw, Save, Timer, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/hub/kpi";
import { Meter, Sparkline } from "@/app/dashboard/command-center";
import { cn } from "@/lib/utils";
import type { RepairComparison, RepairSide, ValueReport } from "@/lib/value-types";
import { saveBaselines } from "./actions";

const hours = (h: number | null | undefined) => (h == null ? "-" : h >= 100 ? Math.round(h).toLocaleString() : h.toFixed(1));
const usd = (v: number | null | undefined) => (v == null ? "-" : `$${Math.round(v).toLocaleString()}`);
const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);
const minutes = (m: number | null | undefined) => {
  if (m == null) return "-";
  if (m < 60) return `${Math.round(m)} min`;
  if (m < 1440) return `${Math.floor(m / 60)} h${Math.round(m % 60) ? ` ${Math.round(m % 60)} min` : ""}`;
  return `${(m / 1440).toFixed(1)} d`;
};

function Badge({ tone, children }: { tone: "measured" | "assumed" | "edited" | "muted"; children: React.ReactNode }) {
  const cls = { measured: "bg-emerald-50 text-emerald-700 ring-emerald-200", assumed: "bg-amber-50 text-amber-800 ring-amber-200",
    edited: "bg-sky-50 text-sky-700 ring-sky-200", muted: "bg-muted text-muted-foreground ring-border" }[tone];
  return <span className={cn("inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", cls)}>{children}</span>;
}

function Headline({ label, value, hint, icon: Icon }: { label: string; value: string; hint: string; icon: typeof Clock }) {
  return (
    <div className="surface p-4">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground"><Icon className="h-3.5 w-3.5" />{label}</div>
      <p className="mt-2 text-[28px] font-semibold leading-none tracking-tight tabular-nums">{value}</p>
      <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

/** The value ledger page body: headline, ledger table, weekly trend, repair times, knowledge growth, baseline editor. */
export function ValueView({ data, canEdit }: { data: ValueReport; canEdit: boolean }) {
  const [editing, setEditing] = useState(false);
  const v = data.value;
  const k = data.knowledge;
  const maxHours = Math.max(1, ...data.weekly.map((w) => w.hours));
  return (
    <div className="space-y-5" data-testid="value-ledger">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Headline icon={Clock} label={`Hours saved, ${data.window_days} days`} value={hours(v.hours)}
                  hint={`${hours(data.hours_by_group.build)} building pipelines · ${hours(data.hours_by_group.ops)} running them`} />
        <Headline icon={Coins} label="Net value" value={v.net_usd == null ? usd(v.value_usd) : usd(v.net_usd)}
                  hint={v.needs_price ? `${usd(v.value_usd)} of time, AI cost not priced: set a credit price in Admin`
                    : `${usd(v.value_usd)} of time minus ${usd(v.ai_cost_usd)} AI cost (${(v.ai_credits ?? 0).toFixed(1)} credits)`} />
        <Headline icon={Gauge} label="Failures fixed automatically" value={pct(k?.components.auto_fix_share.value)}
                  hint={k?.components.auto_fix_share.denominator ? `${k.components.auto_fix_share.numerator} of ${k.components.auto_fix_share.denominator} failures handled` : "no failures in this window"} />
        <Headline icon={BookOpen} label="Knowledge score" value={k?.score == null ? "-" : String(k.score)}
                  hint={k?.previous_score == null ? "out of 100, from measured coverage" : `was ${k.previous_score} the window before`} />
      </div>

      <Panel title="Where the hours come from" icon={Clock}
             action={canEdit && <Button variant="outline" size="sm" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" /> Edit baselines</Button>}>
        <p className="mb-3 text-xs text-muted-foreground">
          Each row multiplies what the platform did (measured) by the minutes the same work takes a person (an assumption).
          Work a person changed counts for part of its time; rejected work counts for nothing.
          Blended rate {usd(data.baselines.blended_rate_usd)} an hour {data.baselines.rate_basis === "edited" ? <Badge tone="edited">admin set</Badge> : <Badge tone="assumed">assumed default</Badge>}
          {data.baselines.version ? ` · baselines version ${data.baselines.version}${data.baselines.changed_by ? ` by ${data.baselines.changed_by}` : ""}` : ""}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="value-table">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr><th className="py-2 pr-3 font-medium">Activity</th><th className="px-3 py-2 text-right font-medium">Count</th>
                <th className="px-3 py-2 text-right font-medium">Credited</th><th className="px-3 py-2 text-right font-medium">Minutes each</th>
                <th className="px-3 py-2 text-right font-medium">Hours</th></tr>
            </thead>
            <tbody className="divide-y">
              {data.activities.map((a) => (
                <tr key={a.activity} className={cn(!a.available && "text-muted-foreground")}>
                  <td className="py-2 pr-3"><p className="font-medium">{a.label}</p><p className="text-[11px] text-muted-foreground">{a.source}</p></td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.available ? <>{a.count?.toLocaleString()} <Badge tone="measured">measured</Badge></> : <Badge tone="muted">not available</Badge>}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.credited ?? "-"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.minutes_each} <Badge tone={a.minutes_basis === "admin set" ? "edited" : "assumed"}>{a.minutes_basis}</Badge></td>
                  <td className="px-3 py-2 text-right font-semibold tabular-nums">{hours(a.hours)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Week by week" icon={Coins}>
          {data.weekly.length ? (
            <>
              <Sparkline values={data.weekly.map((w) => w.hours)} className="h-16" />
              <ul className="mt-3 space-y-1.5 text-xs">
                {data.weekly.slice(-8).map((w) => (
                  <li key={w.week} className="flex items-center gap-3">
                    <span className="w-20 shrink-0 tabular-nums text-muted-foreground">{w.week}</span>
                    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><span className="block h-full rounded-full bg-primary" style={{ width: `${(100 * w.hours) / maxHours}%` }} /></span>
                    <span className="w-16 shrink-0 text-right tabular-nums">{hours(w.hours)} h</span>
                    <span className="w-24 shrink-0 text-right tabular-nums text-muted-foreground">{w.ai_cost_usd == null ? `${w.ai_credits.toFixed(1)} cr` : usd(w.ai_cost_usd)} AI</span>
                  </li>
                ))}
              </ul>
            </>
          ) : <p className="text-sm text-muted-foreground">No activity in this window yet.</p>}
        </Panel>

        <Panel title="Repair time" icon={Timer}>
          <p className="mb-3 text-xs text-muted-foreground">How long a failure stayed broken (median), from the incident timeline. Kept apart from hours saved: downtime avoided is not engineer time.</p>
          {data.repair_time ? (
            <div className="space-y-3">{data.repair_time.map((c) => <Repair key={c.label} c={c} minSamples={data.baselines.min_samples} />)}</div>
          ) : <p className="text-sm text-muted-foreground">Incident history is not available.</p>}
        </Panel>
      </div>

      {k && (
        <Panel title="What the platform knows" icon={BookOpen}>
          <div className="grid gap-4 md:grid-cols-3">
            {([["auto_fix_share", "Failures fixed by a known fix"], ["dag_coverage", "DAGs with a profile in Knowledge"],
               ["fix_coverage", "Failing errors with a known fix"]] as const).map(([key, label]) => {
              const c = k.components[key];
              return (
                <div key={key}>
                  <div className="flex items-baseline justify-between text-sm"><span>{label}</span><span className="font-semibold tabular-nums">{pct(c.value)}</span></div>
                  <div className="mt-1.5"><Meter value={c.value == null ? null : c.value * 100} tone="primary" /></div>
                  <p className="mt-1 text-[11px] text-muted-foreground">{c.denominator ? `${c.numerator} of ${c.denominator}` : "nothing to measure yet"}{c.previous != null ? ` · was ${pct(c.previous)}` : ""}</p>
                </div>
              );
            })}
          </div>
          <p className="mt-4 text-xs text-muted-foreground">
            {k.counts.knowledge_items ?? 0} knowledge items in use · {k.counts.knowledge_new ?? 0} learned in this window · {k.counts.known_fixes ?? 0} published known fixes · {k.counts.runbooks ?? 0} runbooks
          </p>
        </Panel>
      )}

      {editing && <BaselineEditor data={data} onClose={() => setEditing(false)} />}
    </div>
  );
}

function Repair({ c, minSamples }: { c: RepairComparison; minSamples: number }) {
  const sides = Object.entries(c).filter(([, v]) => v && typeof v === "object" && "median_min" in (v as object)) as [string, RepairSide][];
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">{c.label}
        {c.measured ? <Badge tone="measured">measured</Badge> : <Badge tone="muted">needs {minSamples}+ on each side</Badge>}
        {c.change_pct != null && <span className={cn("ml-auto text-sm font-semibold tabular-nums", c.change_pct < 0 ? "text-emerald-700" : "text-rose-700")}>{c.change_pct > 0 ? "+" : ""}{c.change_pct}%</span>}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {sides.map(([name, s]) => <span key={name}><span className="capitalize">{name}</span>: <span className="font-medium text-foreground">{minutes(s.median_min)}</span> ({s.n})</span>)}
        {c.failures != null && <span>{c.failures} failures seen both ways</span>}
        {c.cutover && <span>first automatic fix {c.cutover.slice(0, 10)}</span>}
      </div>
    </div>
  );
}

function BaselineEditor({ data, onClose }: { data: ValueReport; onClose: () => void }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [rate, setRate] = useState(String(data.baselines.blended_rate_usd));
  const [mins, setMins] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(data.baselines.activities).map(([k, a]) => [k, String(a.minutes)])));
  const save = (reset = false) => start(async () => {
    setError("");
    const activities = Object.fromEntries(Object.entries(mins).map(([k, m]) => [k, { minutes: Number(m) }]));
    const r = await saveBaselines(reset ? {} : { activities, blended_rate_usd: Number(rate) }, reset);
    if (!r.ok) { setError(r.error); return; }
    onClose();
    router.refresh();
  });
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" role="dialog" aria-modal="true" aria-label="Edit baselines">
      <div className="h-full w-full max-w-md overflow-y-auto bg-background p-5 shadow-xl">
        <div className="mb-4 flex items-center gap-2">
          <h2 className="text-base font-semibold">Effort baselines</h2>
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></Button>
        </div>
        <p className="mb-4 text-xs text-muted-foreground">Minutes the same work takes a person at this client. Each save is a new version, kept with your name.</p>
        <label className="mb-4 block text-sm font-medium">Blended rate (USD per hour)
          <Input value={rate} onChange={(e) => setRate(e.target.value)} inputMode="decimal" className="mt-1" />
        </label>
        <div className="space-y-3">
          {Object.entries(data.baselines.activities).map(([key, a]) => (
            <label key={key} className="block text-sm">
              <span className="font-medium">{a.label}</span> <span className="text-xs text-muted-foreground">minutes per {a.unit} (default {a.default_minutes})</span>
              <Input value={mins[key]} onChange={(e) => setMins({ ...mins, [key]: e.target.value })} inputMode="decimal" className="mt-1" />
            </label>
          ))}
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
        <div className="mt-5 flex gap-2">
          <Button onClick={() => save(false)} disabled={pending}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save</Button>
          <Button variant="outline" onClick={() => save(true)} disabled={pending}><RotateCcw className="h-4 w-4" /> Reset to defaults</Button>
        </div>
      </div>
    </div>
  );
}
