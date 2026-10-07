import Link from "next/link";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { AuditEvent } from "@/lib/types";
import { AuditTable } from "@/components/audit-table";
import { Card } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { AuditTabs, CostFilters, EventFilters } from "./audit-controls";

const LIMIT = 100;

type CostRow = {
  key: string; run_name?: string | null; calls: number; input_tokens: number; output_tokens: number;
  total_tokens: number; estimated_cost: number; duration_ms: number;
};

type Params = { tab?: string; q?: string; actor_type?: string; since?: string; until?: string; offset?: string; group_by?: string };

const n = (v: unknown) => Number(v ?? 0);

export default async function Audit({ searchParams }: { searchParams?: Params }) {
  const sp = searchParams ?? {};
  const tab = sp.tab === "cost" ? "cost" : "events";
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Platform" title="Audit"
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
  const { events, total } = await api<{ events: AuditEvent[]; total?: number }>(`/api/audit?${query.toString()}`)
    .catch(() => ({ events: [] as AuditEvent[], total: 0 }));
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
  const data = await api<{ rows: CostRow[]; totals: Record<string, number> }>(`/api/costs?${query.toString()}`)
    .catch(() => ({ rows: [] as CostRow[], totals: {} as Record<string, number> }));
  const t = data.totals;
  return (
    <div className="space-y-3">
      <CostFilters rows={data.rows as unknown as Record<string, unknown>[]} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["AI calls", n(t.calls).toLocaleString()], ["Tokens", n(t.total_tokens).toLocaleString()],
          ["Input / output", `${n(t.input_tokens).toLocaleString()} / ${n(t.output_tokens).toLocaleString()}`],
          ["Estimated credits", n(t.estimated_cost).toFixed(3)]].map(([label, value]) => (
          <Card key={label} className="p-4">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-lg font-semibold tabular-nums">{value}</p>
          </Card>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Estimates use the credit rates set in Admin. A rate of 0 means the model has no rate yet, so its cost shows as 0.
      </p>
      <Card>
        <Table>
          <THead>
            <TR><TH>{groupBy === "run" ? "Run" : groupBy === "day" ? "Day" : groupBy === "model" ? "Model" : "Stage"}</TH>
              <TH>Calls</TH><TH>Tokens</TH><TH>Avg time</TH><TH>Estimated credits</TH></TR>
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
                <TD className="tabular-nums">{n(r.estimated_cost).toFixed(3)}</TD>
              </TR>
            ))}
            {data.rows.length === 0 && (
              <TR><TD colSpan={5} className="py-6 text-center text-muted-foreground">No AI usage recorded in this range.</TD></TR>
            )}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}
