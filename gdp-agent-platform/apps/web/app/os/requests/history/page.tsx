import Link from "next/link";
import { Ban, CheckCircle2, Clock, History, X } from "lucide-react";
import { loadRequests } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import type { OnboardingRequest } from "@/lib/os/requests";
import { Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "Request history" };

const ENDED: Record<string, { tone: Tone; label: string }> = {
  DONE: { tone: "green", label: "Done" }, CANCELLED: { tone: "neutral", label: "Cancelled" }, REJECTED: { tone: "amber", label: "Rejected" },
};

/** Days from filing to closing, or null when either date is missing. */
function days(r: OnboardingRequest): number | null {
  if (!r.closed_at) return null;
  const ms = new Date(r.closed_at).getTime() - new Date(r.created_at).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms / 86_400_000 : null;
}
const span = (d: number | null) => (d === null ? "–" : d < 1 ? `${Math.max(1, Math.round(d * 24))} h` : `${d.toFixed(d < 10 ? 1 : 0)} days`);

/** Finished requests (done, cancelled, rejected), newest first: what was asked, how it ended, who closed it and how long
 *  it took. Each one opens with its whole timeline. Read only and open to everyone signed in (each sees the requests of
 *  the domains they may see). */
export default async function RequestHistory() {
  const access = await guard("requestHistory");
  const list = await loadRequests("all", "CLOSED");
  const closed = [...list.data.requests].sort((a, b) => (b.closed_at ?? b.updated_at).localeCompare(a.closed_at ?? a.updated_at));
  const done = closed.filter((r) => r.status === "DONE");
  const spans = done.map(days).filter((d): d is number => d !== null).sort((a, b) => a - b);
  const median = spans.length ? spans[Math.floor(spans.length / 2)] : null;

  return (
    <>
      <PageHeader eyebrow={`${access.persona.label} / Intake`} title="Request history"
                  subtitle="Every finished onboarding request: what was asked, how it ended, who closed it and how long it took. Open one for its whole timeline." />
      <SourceError what="Requests" error={list.error} />
      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Finished requests" value={closed.length} icon={History} tone="blue" />
        <Kpi label="Done" value={done.length} hint="every part onboarded" icon={CheckCircle2} tone="green" />
        <Kpi label="Cancelled or rejected" value={closed.length - done.length} icon={Ban} tone={closed.length - done.length ? "amber" : "green"} />
        <Kpi label="Median time to done" value={span(median)} hint="from filing to the last part done" icon={Clock} tone="purple" />
      </div>
      <Card title="Finished requests" subtitle="Newest first" pad={false}>
        {closed.length ? (
          <div className="overflow-x-auto" data-testid="request-history">
            <table className="os-table">
              <thead><tr><th>Request</th><th>Domain</th><th>Sources</th><th>Ended</th><th>Closed by</th><th>Took</th></tr></thead>
              <tbody>
                {closed.map((r) => {
                  const e = ENDED[r.status] ?? { tone: "neutral" as Tone, label: pretty(r.status) };
                  const sources = r.items.filter((i) => !i.removed_at);
                  return (
                    <tr key={r.request_id}>
                      <td className="max-w-[300px]">
                        <Link href={`/os/requests/${r.request_id}`} className="font-semibold underline-offset-2 hover:underline">{r.title}</Link>
                        <div className="os-faint text-[10.5px]">{pretty(r.request_type)} · filed by {r.requested_by} {ago(r.created_at)}</div>
                      </td>
                      <td>{r.domain_name ?? r.proposed_domain ?? <span className="os-faint">none</span>}</td>
                      <td className="max-w-[200px] truncate text-[11.5px]" title={sources.map((i) => i.source_name).join(", ")}>
                        {sources.length ? sources.map((i) => i.source_name).join(", ") : <span className="os-faint">none</span>}
                      </td>
                      <td><Tag tone={e.tone}>{e.label}</Tag>{r.close_reason && <div className="os-faint mt-0.5 max-w-[220px] truncate text-[10.5px]" title={r.close_reason}>{r.close_reason}</div>}</td>
                      <td className="text-[11.5px]">{r.closed_by ?? "–"}{r.closed_at && <div className="os-faint text-[10.5px]">{ago(r.closed_at)}</div>}</td>
                      <td className="tabular-nums">{span(days(r))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <div className="p-5"><Empty icon={X} title="No finished request yet">Requests appear here once they are done, cancelled or rejected.</Empty></div>}
      </Card>
    </>
  );
}
