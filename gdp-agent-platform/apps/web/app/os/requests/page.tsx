import Link from "next/link";
import { AlertTriangle, CalendarClock, Inbox, Plus } from "lucide-react";
import { loadRequests } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { has, hasAny } from "@/lib/os/access";
import { PRIORITY_TONE, overdue, phaseOf, stageOwner } from "@/lib/os/requests";
import { RequestDots } from "@/components/os/request-dots";
import { Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Onboarding requests" };

const VIEWS = [
  { key: "waiting", label: "Waiting on me", scope: "waiting", status: "OPEN" },
  { key: "mine", label: "Filed by me", scope: "mine", status: "ALL" },
  { key: "open", label: "All open", scope: "all", status: "OPEN" },
  { key: "closed", label: "Closed", scope: "all", status: "CLOSED" },
] as const;

export default async function Requests({ searchParams }: { searchParams: { view?: string } }) {
  const access = await guard("requests");
  const view = VIEWS.find((v) => v.key === searchParams.view) ?? VIEWS[0];
  const [list, open] = await Promise.all([loadRequests(view.scope, view.status), view.key === "open" ? null : loadRequests("all", "OPEN")]);
  const all = (open ?? list).data;
  // "waiting on me" is the grants on show (a previewed role's), not only the signed-in person's
  const rows = view.key === "waiting" ? list.data.requests.filter((r) => r.waits.some((w) => has(access, w.privilege))) : list.data.requests;
  const files = hasAny(access, ["DOMAIN.EDIT", "RUN.CREATE", "REQUEST.CHANGES"]);

  return (
    <>
      <PageHeader eyebrow={`${access.persona.label} / Onboarding requests`} title="Onboarding requests"
                  subtitle="What data owners asked for, where each request is, and whose turn it is. The stage is worked out from the domain, the sources and their runs; nobody types it in."
                  actions={files ? <Btn href="/os/intake" variant="primary"><Plus className="h-3.5 w-3.5" /> New request</Btn> : undefined} />
      <SourceError what="Requests" error={list.error} />

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-5">
        {(["ENABLE", "DESIGN", "BUILD", "REVIEW"] as const).map((s) => (
          <Kpi key={s} label={`In ${s.toLowerCase()}`} value={all.counts[s] ?? 0} hint={stageOwner(s)} icon={Inbox}
               tone={s === "ENABLE" ? "amber" : s === "DESIGN" ? "blue" : s === "BUILD" ? "purple" : "green"} />
        ))}
        <Kpi label="Need attention" value={all.requests.filter((r) => r.attention).length} hint="a failed run or a source that is gone" icon={AlertTriangle}
             tone={all.requests.some((r) => r.attention) ? "red" : "green"} />
      </div>

      <Card pad={false}>
        <div className="flex flex-wrap gap-x-5 border-b border-[var(--os-line)] px-5" role="tablist">
          {VIEWS.map((v) => <Link key={v.key} href={`/os/requests?view=${v.key}`} role="tab" aria-selected={v.key === view.key} className="os-tab">{v.label}</Link>)}
        </div>
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="os-table">
              <thead><tr><th>Request</th><th>Path</th><th>Waiting on</th><th>Priority</th><th>Requested</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.request_id}>
                    <td className="max-w-[320px]">
                      <Link href={`/os/requests/${r.request_id}`} className="block truncate font-bold hover:underline" title={r.title}>{r.title}</Link>
                      <div className="os-faint truncate text-[11px]">{pretty(r.request_type)} · {r.domain_name ?? (r.proposed_domain ? `new domain ${r.proposed_domain}` : "no domain")} ·{" "}
                        {r.items.length ? `${r.items.filter((i) => !i.removed_at).length} source${r.items.filter((i) => !i.removed_at).length === 1 ? "" : "s"}` : "no source"}</div>
                    </td>
                    <td><RequestDots r={r} /><div className="os-faint mt-1 text-[10.5px]">{r.status === "OPEN" ? phaseOf(r).label : pretty(r.status)}</div></td>
                    <td className="max-w-[300px]">
                      {r.status === "OPEN" ? (
                        <>
                          <div className="truncate text-[12px] font-semibold" title={r.waiting_label ?? undefined}>{r.waiting_label ?? "Nobody"}</div>
                          {r.attention && <div className="truncate text-[11px] text-[var(--os-red-ink)]" title={r.attention}><AlertTriangle className="mr-1 inline h-3 w-3" />{r.attention}</div>}
                          {r.waits.some((w) => has(access, w.privilege)) && <Tag tone="green" className="mt-1">Your turn</Tag>}
                        </>
                      ) : <span className="os-faint text-[11.5px]">{r.close_reason ?? (r.status === "DONE" ? "Done" : pretty(r.status))}</span>}
                    </td>
                    <td>
                      <Tag tone={PRIORITY_TONE[r.priority] ?? "neutral"}>{pretty(r.priority)}</Tag>
                      {r.needed_by && <div className={cn("mt-1 flex items-center gap-1 text-[10.5px]", overdue(r) ? "text-[var(--os-red-ink)]" : "os-faint")}>
                        <CalendarClock className="h-3 w-3" />{r.needed_by.slice(0, 10)}{overdue(r) ? " · overdue" : ""}</div>}
                    </td>
                    <td className="os-muted whitespace-nowrap text-[11.5px]">{r.requested_by}<div className="os-faint">{ago(r.created_at)}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-5">
            <Empty icon={Inbox} title={view.key === "waiting" ? "Nothing waits on you" : "No requests here"}
                   action={files ? <Btn href="/os/intake" variant="primary">File a request</Btn> : undefined}>
              {view.key === "waiting" ? "When a request needs your role (a source to register, a run to start, a gate to decide), it shows here and on My Work." : undefined}
            </Empty>
          </div>
        )}
      </Card>
    </>
  );
}
