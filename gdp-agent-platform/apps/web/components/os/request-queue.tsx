import Link from "next/link";
import { AlertTriangle, Inbox } from "lucide-react";
import { has, type Grants } from "@/lib/os/access";
import { PRIORITY_TONE, overdue, type OnboardingRequest } from "@/lib/os/requests";
import { Btn, Card, Empty, SourceError, Tag, ago, pretty } from "./ui";
import { RequestDots } from "./request-dots";

/** Onboarding requests with the part each one waits on, for the screens where that role works (My Work for everyone,
 *  Enable for the administrator, Design for the designer). Only waits the viewer's privileges cover are named "yours". */
export function RequestQueue({ title, subtitle, requests, access, error, empty, max = 6 }: {
  title: string; subtitle: string; requests: OnboardingRequest[]; access: Grants; error?: string | null; empty: string; max?: number;
}) {
  return (
    <Card title={title} subtitle={subtitle} pad={false} action={<Btn href="/os/requests">All requests</Btn>}>
      {error && <div className="px-5 pt-3"><SourceError what="Requests" error={error} /></div>}
      {requests.length ? (
        <div className="os-divide">
          {requests.slice(0, max).map((r) => {
            const yours = r.waits.filter((w) => has(access, w.privilege));
            const wait = yours[0] ?? r.waits[0];
            return (
              <Link key={r.request_id} href={`/os/requests/${r.request_id}`} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 os-hover">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-bold">{r.title}</span>
                  <span className="os-faint block truncate text-[11px]">{r.domain_name ?? r.proposed_domain ?? "No domain"} · {wait?.label ?? r.waiting_label ?? "Nothing waiting"}</span>
                  {r.attention && <span className="block truncate text-[11px] text-[var(--os-red-ink)]"><AlertTriangle className="mr-1 inline h-3 w-3" />{r.attention}</span>}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <RequestDots r={r} />
                  <Tag tone={PRIORITY_TONE[r.priority] ?? "neutral"}>{pretty(r.priority)}</Tag>
                  {overdue(r) && <Tag tone="red">Overdue</Tag>}
                  {yours.length > 0 && <Tag tone="green">Your turn</Tag>}
                  <span className="os-faint text-[10.5px]">{ago(r.updated_at)}</span>
                </span>
              </Link>
            );
          })}
        </div>
      ) : <div className="p-5"><Empty icon={Inbox} title={empty} /></div>}
    </Card>
  );
}
