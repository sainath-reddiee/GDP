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

/** Onboarding a source an onboarding request asked for (Enable, "Onboard for this request"): says which request it
 *  is for and how it comes back, and links back to it. The wizard itself is unchanged apart from the name it starts from. */
export async function RequestHandoff({ requestId, itemId }: { requestId: string; itemId?: string }) {
  const request = await api<RequestDetail>(`/api/onboarding-requests/${encodeURIComponent(requestId)}`).catch(() => null);
  if (!request) {
    return (
      <div role="status" className="surface mb-4 flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Inbox className="h-4 w-4" /> The onboarding request this was opened for could not be read. Register the source, then link it from the request.
      </div>
    );
  }
  const item = request.items.find((i) => i.item_id === itemId) ?? null;
  const name = item ? sourceNameFor(item.source_name) : null;
  return (
    <div role="status" data-testid="request-handoff" className="surface mb-4 flex flex-wrap items-start justify-between gap-3 border-primary/30 bg-accent/40 p-4">
      <div className="min-w-0 text-sm">
        <p className="font-semibold"><Inbox className="mr-1.5 inline h-4 w-4" />Onboarding {item ? item.source_name : "a source"} for the request &ldquo;{request.title}&rdquo;</p>
        <p className="mt-1 text-muted-foreground">
          Filed by {request.requested_by}{request.domain_name ? ` for ${request.domain_name}` : ""}.
          {name ? <> Register it as <span className="font-mono font-medium text-foreground">{name}</span> (filled in below) and the request attaches it on its own.</> : " Link the source from the request once it is registered."}
          {" "}The request reads Source onboarded once the source is reachable and landed, and moves on to the Data Designer.
        </p>
        {item?.source_note && <p className="mt-1 text-muted-foreground">What is needed: {item.source_note}</p>}
      </div>
      <Link href={`/os/requests/${request.request_id}`} className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to the request
      </Link>
    </div>
  );
}
