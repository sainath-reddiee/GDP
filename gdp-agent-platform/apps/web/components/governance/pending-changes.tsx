import Link from "next/link";
import { Clock3 } from "lucide-react";
import { api } from "@/lib/api";

type Pending = { request_id: string; title: string; summary: string; requested_by: string; approver_role: string; created_at: string; can_decide: boolean };

/** Changes to this asset waiting for approval (store edits always queue). Hidden when there are none or the
 *  governance API is not available. */
export async function PendingChanges({ path, what = "change" }: { path: string; what?: string }) {
  const data = await api<{ requests: Pending[] }>(`/api/governance/requests/pending?path=${encodeURIComponent(path)}`).catch(() => null);
  const pending = data?.requests ?? [];
  if (!pending.length) return null;
  return (
    <section className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-100" data-testid="pending-changes">
      <p className="flex items-center gap-2 font-medium">
        <Clock3 className="h-4 w-4" /> {pending.length} {what}{pending.length === 1 ? "" : "s"} waiting for approval
        <Link href="/approvals" className="ml-auto text-xs font-medium underline">Open Approvals</Link>
      </p>
      <ul className="mt-2 space-y-1">
        {pending.map((p) => (
          <li key={p.request_id} className="flex flex-wrap items-center gap-x-2 text-xs">
            <span className="font-medium">{p.summary || p.title}</span>
            <span className="text-blue-700/80 dark:text-blue-300/80">by {p.requested_by} · for {p.approver_role} · {p.created_at.slice(0, 16)}</span>
            {p.can_decide && <Link href="/approvals" className="rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-semibold text-white">You can approve</Link>}
          </li>
        ))}
      </ul>
    </section>
  );
}
