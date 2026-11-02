import Link from "next/link";
import { ArrowLeft, Inbox } from "lucide-react";
import { api } from "@/lib/api";
import type { RequestDetail } from "@/lib/os/requests";

/** The name a request's source is registered under: letters, digits and _ as the wizards require ("Yardi Voyager"
 *  becomes YARDI_VOYAGER). The request matches it back ignoring case and punctuation, so registering under this name
 *  attaches the source to the request on its own. */
export function sourceNameFor(name: string): string {
  const slug = name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return (/^[A-Z]/.test(slug) ? slug : `S_${slug}`).slice(0, 64);
}

/** Onboarding a source an onboarding request asked for (Enable, "Onboard for this request"): one line saying which
 *  request it is for, with a link back. The wizard starts from the request's source name and the request attaches the
 *  source on its own. */
export async function RequestHandoff({ requestId, itemId }: { requestId: string; itemId?: string }) {
  const request = await api<RequestDetail>(`/api/onboarding-requests/${encodeURIComponent(requestId)}`).catch(() => null);
  if (!request) {
    return (
      <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Inbox className="h-4 w-4" /> The request this was opened for could not be read. Register the source, then link it from the request.
      </div>
    );
  }
  const item = request.items.find((i) => i.item_id === itemId) ?? null;
  return (
    <div role="status" data-testid="request-handoff" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-2.5">
      <p className="flex min-w-0 items-center gap-2 text-sm">
        <Inbox className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="truncate">For <span className="font-semibold">{request.title}</span>{item ? <> · {item.source_name}</> : null}</span>
      </p>
      <Link href={`/os/requests/${request.request_id}`} className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to the request
      </Link>
    </div>
  );
}
