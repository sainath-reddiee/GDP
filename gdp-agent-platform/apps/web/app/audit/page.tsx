import Link from "next/link";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { AuditEvent } from "@/lib/types";
import { AuditTable } from "@/components/audit-table";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { AuditTabs, CostFilters, EventFilters } from "./audit-controls";
import { ReconcileButton } from "./reconcile-button";
import type { ReconcileResult } from "@/app/admin/actions";

const LIMIT = 100;

type CostRow = {
  key: string; run_name?: string | null; calls: number; input_tokens: number; output_tokens: number;
  total_tokens: number; estimated_cost: number; duration_ms: number;
  credits?: number; actual_credits?: number | null; estimated_credits?: number; actual_calls?: number;
};
type CostData = { rows: CostRow[]; totals: Record<string, number>; credit_price_usd?: number | null; reconcile?: ReconcileResult | null };

type Params = { tab?: string; q?: string; actor_type?: string; since?: string; until?: string; offset?: string; group_by?: string };

const n = (v: unknown) => Number(v ?? 0);

export default async function Audit({ searchParams }: { searchParams?: Params }) {
  const sp = searchParams ?? {};
  const tab = sp.tab === "cost" ? "cost" : "events";
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Govern" title="Audit"
                  description="Every workflow event across runs, and what the AI steps used and cost." />
      <AuditTabs tab={tab} />
      {tab === "events" ? <Events sp={sp} /> : <Cost sp={sp} />}
    </div>
  );
}

async function Events({ sp }: { sp: Params }) {
  const offset = Math.max(0, Number(sp.offset ?? 0) || 0);
  const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
  for (const key of ["q", "actor_type", "since", "until"] as const) {
    const v = sp[key];
    if (v) query.set(key, v);
  }
  let failure = "";
  const { events, total } = await api<{ events: AuditEvent[]; total?: number }>(`/api/audit?${query.toString()}`)
    .catch((e: Error) => { failure = e.message; return { events: [] as AuditEvent[], total: 0 }; });
  if (failure) return <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">The audit trail could not be read: {failure}</p>;
  return (
    <div className="space-y-3">
      <EventFilters rows={events as unknown as Record<string, unknown>[]} total={total ?? events.length} offset={offset} limit={LIMIT} />
      <AuditTable events={events} showRun />
    </div>
  );
}

async function Cost({ sp }: { sp: Params }) {
  const groupBy = ["stage", "model", "run", "day"].includes(sp.group_by ?? "") ? sp.group_by! : "stage";
  const query = new URLSearchParams({ group_by: groupBy, limit: "200" });
  if (sp.since) query.set("since", sp.since);
  if (sp.until) query.set("until", sp.until);
  let failure = "";
  const data = await api<CostData>(`/api/costs?${query.toString()}`)
    .catch((e: Error): CostData => { failure = e.message; return { rows: [], totals: {} }; });
  if (failure) return <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">AI usage could not be read: {failure}</p>;
  const t = data.totals;
  const credits = n(t.credits ?? t.estimated_cost);
  const price = data.credit_price_usd ?? null;
  return (
    <div className="space-y-3">
      <CostFilters rows={data.rows as unknown as Record<string, unknown>[]} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["AI calls", n(t.calls).toLocaleString()], ["Tokens", n(t.total_tokens).toLocaleString()],
          ["Input / output", `${n(t.input_tokens).toLocaleString()} / ${n(t.output_tokens).toLocaleString()}`],
          ["Credits", credits.toFixed(3),
           `${n(t.actual_credits).toFixed(3)} billed · ${n(t.estimated_credits).toFixed(3)} estimated${price ? ` · about $${(credits * price).toFixed(2)}` : ""}`]]
          .map(([label, value, hint]) => (
          <Card key={label} className="p-4">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-lg font-semibold tabular-nums">{value}</p>
            {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
          </Card>
        ))}
      </div>
      <ReconcileButton last={data.reconcile} />
      <p className="text-xs text-muted-foreground">
        Billed credits come from Snowflake&apos;s Cortex usage views, matched by query id (they lag by up to a few hours).
        Until then a call shows an estimate from the Admin rate card, or from this account&apos;s own billed rate for that model.
      </p>
      <Card>
        <Table>
          <THead>
            <TR><TH>{groupBy === "run" ? "Run" : groupBy === "day" ? "Day" : groupBy === "model" ? "Model" : "Stage"}</TH>
              <TH>Calls</TH><TH>Tokens</TH><TH>Avg time</TH><TH>Credits</TH><TH>Basis</TH></TR>
          </THead>
          <TBody>
            {data.rows.map((r) => (
              <TR key={r.key ?? "none"}>
                <TD>
                  {groupBy === "run" && r.key
                    ? <Link href={`/runs/${r.key}`} className="text-primary hover:underline">{r.run_name ?? r.key.slice(0, 8)}</Link>
                    : (r.key ?? "not linked to a run")}
                </TD>
                <TD className="tabular-nums">{n(r.calls).toLocaleString()}</TD>
                <TD className="tabular-nums">{n(r.total_tokens).toLocaleString()}</TD>
                <TD className="tabular-nums">{n(r.calls) ? `${(n(r.duration_ms) / n(r.calls) / 1000).toFixed(1)}s` : "-"}</TD>
                <TD className="tabular-nums">{n(r.credits ?? r.estimated_cost).toFixed(3)}</TD>
                <TD className="text-xs text-muted-foreground">
                  {n(r.actual_calls) >= n(r.calls) && n(r.calls) > 0 ? "billed"
                    : n(r.actual_calls) > 0 ? `${n(r.actual_calls)} of ${n(r.calls)} billed` : "estimated"}
                </TD>
              </TR>
            ))}
            {data.rows.length === 0 && (
              <TR><TD colSpan={6} className="py-6 text-center text-muted-foreground">No AI usage recorded in this range.</TD></TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
