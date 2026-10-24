"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { Ban, Check, Link2, Loader2, MessageSquare, Play, Plus, RotateCcw, Send, Trash2, Unlink, X } from "lucide-react";
import {
  addRequestSource, cancelRequest, commentRequest, createRequest, linkRequestDomain, linkRequestRun, linkRequestSource, rejectRequest,
  removeRequestSource, reopenRequest, startEnhancementRun, type NewRequestInput, type RequestResult,
} from "@/app/os/requests/actions";
import type { Duplicate, RequestDetail, RequestItem } from "@/lib/os/requests";
import { cn } from "@/lib/utils";

// The request controls of the /os screens. Each one calls the API (app/os/requests/actions.ts), which checks who may
// make the change; the page passes what the caller may do (`may`, from the API) so nothing is offered that would be
// refused. While an admin previews another role every control is off: the API would act as the admin.

type Outcome = { tone: "done" | "fail"; text: string } | null;

export function useRequestCall() {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [outcome, setOutcome] = useState<Outcome>(null);
  const run = (fn: () => Promise<RequestResult>, done: string, after?: (r: RequestResult & { ok: true }) => void) => start(async () => {
    setOutcome(null);
    const r = await fn();
    if (!r.ok) { setOutcome({ tone: "fail", text: r.error }); return; }
    setOutcome({ tone: "done", text: done });
    if (after) after(r); else router.refresh();
  });
  return { pending, outcome, run, setOutcome };
}

export function Line({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return <div role={outcome.tone === "fail" ? "alert" : "status"} className={cn("os-banner mt-2 text-[12px]", outcome.tone === "fail" && "os-banner-red")}>{outcome.text}</div>;
}

export function B({ children, onClick, disabled, variant = "default", title, testId, type = "button" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; variant?: "default" | "primary" | "warn" | "red" | "mint";
  title?: string; testId?: string; type?: "button" | "submit";
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} data-testid={testId}
            className={cn("os-btn", variant !== "default" && `os-btn-${variant}`)}>{children}</button>
  );
}

export const Off = () => <p className="os-faint mt-1 text-[11px]">Off while you preview another role: it would run as you.</p>;

/** An Enhance source request's next step for the Data Designer: start the enhancement run of the onboarded source,
 *  carrying what the request asks to add. Shown only to holders of RUN.CREATE while the source has no run. */
export function StartEnhancementRun({ requestId, itemId, source, previewing }: { requestId: string; itemId: string; source: string; previewing: boolean }) {
  const { pending, outcome, run } = useRequestCall();
  return (
    <span className="flex flex-col items-end">
      <B variant="primary" disabled={pending || previewing} testId={`start-enhancement-${itemId.slice(0, 8)}`}
         title={`Start a new run of ${source} in this domain for what the request adds`}
         onClick={() => run(() => startEnhancementRun(requestId, itemId), "Enhancement run started. The Data Designer maps the delta next.")}>
        {pending ? <Loader2 className="os-spin h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />} Start enhancement run
      </B>
      {previewing && <Off />}
      <Line outcome={outcome} />
    </span>
  );
}

/* ---------------------------------------------------------------- filing a request (Intake) */

export function SubmitRequest({ type, intent = "NEW", domainId, domainName, picked, previewing }: {
  type: "SOURCE" | "DOMAIN" | "PRODUCT"; intent?: "NEW" | "ENHANCE"; domainId: string | null; domainName: string | null;
  picked: { source_system_id: string; source_name: string }[]; previewing: boolean;
}) {
  const enhance = intent === "ENHANCE";
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [outcome, setOutcomeText] = useState("");
  const [priority, setPriority] = useState<"LOW" | "MEDIUM" | "HIGH">("MEDIUM");
  const [neededBy, setNeededBy] = useState("");
  const [proposed, setProposed] = useState("");
  const [named, setNamed] = useState<{ name: string; note: string }[]>([]);
  const [draft, setDraft] = useState({ name: "", note: "" });
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);
  const { pending, outcome: result, run } = useRequestCall();

  const sources = [...picked.map((p) => ({ source_system_id: p.source_system_id })), ...named.map((n) => ({ source_name: n.name, note: n.note || undefined }))];
  // enhancing a domain extends one that exists (picked above) and says what it needs; enhancing a source widens a
  // registered one (picked above), so no new source is named
  const ready = !!title.trim() && (type === "DOMAIN" && !enhance ? !!(domainId || proposed.trim()) : !!domainId)
    && (type !== "SOURCE" || sources.length > 0) && (!enhance || type !== "DOMAIN" || !!outcome.trim());
  const missing = !title.trim() ? "Give the request a title." : (type !== "DOMAIN" || enhance) && !domainId ? "Choose the domain above."
    : type === "DOMAIN" && !enhance && !domainId && !proposed.trim() ? "Name the new domain."
    : type === "DOMAIN" && enhance && !outcome.trim() ? "Say what the domain needs."
    : type === "SOURCE" && !sources.length ? (enhance ? "Pick the source to enhance above." : "Pick or name at least one source.") : "";

  const submit = (allowDuplicate = false) => {
    const body: NewRequestInput = {
      request_type: type, intent, title: title.trim(), business_outcome: outcome.trim() || undefined, priority,
      needed_by: neededBy || null, domain_id: domainId, proposed_domain: !domainId && type === "DOMAIN" && !enhance ? proposed.trim() : null,
      sources, allow_duplicate: allowDuplicate,
    };
    setDupes(null);
    run(async () => {
      const r = await createRequest(body);
      if (!r.ok && r.duplicates) setDupes(r.duplicates);
      return r;
    }, "Request filed. Routing it now...", (r) => router.push(`/os/requests/${r.data.request_id}`));
  };

  return (
    <div data-testid="submit-request" className="grid gap-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <div><label className="os-label" htmlFor="rq-title">Request title</label>
          <input id="rq-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} disabled={previewing}
                 placeholder={type === "PRODUCT" ? "e.g. Pipeline forecast by region" : type === "DOMAIN" ? (enhance ? `e.g. Add leases to ${domainName ?? "Property"}` : "e.g. Tenant domain for leasing")
                   : enhance ? "e.g. Salesforce: add campaign tables" : `e.g. ${domainName ?? "Opportunity"} from Salesforce`} className="os-input" /></div>
        <div><label className="os-label" htmlFor="rq-priority">Priority</label>
          <select id="rq-priority" value={priority} onChange={(e) => setPriority(e.target.value as typeof priority)} disabled={previewing} className="os-input">
            <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option>
          </select></div>
        <div><label className="os-label" htmlFor="rq-needed">Needed by</label>
          <input id="rq-needed" type="date" value={neededBy} onChange={(e) => setNeededBy(e.target.value)} disabled={previewing} className="os-input" /></div>
      </div>
      <div><label className="os-label" htmlFor="rq-outcome">{type === "DOMAIN" && enhance ? "What the domain needs" : "Business outcome"}</label>
        <textarea id="rq-outcome" value={outcome} onChange={(e) => setOutcomeText(e.target.value)} rows={type === "DOMAIN" && enhance ? 3 : 2} maxLength={4000} disabled={previewing}
                  placeholder={type === "DOMAIN" && enhance ? "The entities, attributes or rules to add, and what the business gets from them"
                    : type === "SOURCE" && enhance ? "The tables or scope to add, and what the business gets from them" : "What the business gets, the consumers and their SLA"}
                  className="os-input resize-y" /></div>
      {type === "DOMAIN" && !domainId && !enhance && (
        <div><label className="os-label" htmlFor="rq-domain">New domain name</label>
          <input id="rq-domain" value={proposed} onChange={(e) => setProposed(e.target.value)} maxLength={128} disabled={previewing}
                 placeholder="e.g. TENANT" className="os-input" /></div>
      )}
      {type !== "PRODUCT" && !(type === "SOURCE" && enhance) && (
        <div>
          <span className="os-label">Request a new source</span>
          <div className="flex flex-wrap gap-2">
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Source name, e.g. Yardi" maxLength={256}
                   aria-label="Unregistered source name" disabled={previewing} className="os-input min-w-[160px] flex-1" />
            <input value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} placeholder="What is needed from it (optional)" maxLength={2000}
                   aria-label="What is needed from the source" disabled={previewing} className="os-input min-w-[200px] flex-[2]" />
            <B disabled={!draft.name.trim() || previewing || named.some((n) => n.name.toUpperCase() === draft.name.trim().toUpperCase())}
               onClick={() => { setNamed([...named, { name: draft.name.trim(), note: draft.note.trim() }]); setDraft({ name: "", note: "" }); }}>
              <Plus className="h-3.5 w-3.5" /> Add
            </B>
          </div>
          {named.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {named.map((n) => (
                <span key={n.name} className="os-tag os-tag-amber">{n.name}{n.note ? ` · ${n.note.slice(0, 40)}` : ""}
                  <button type="button" aria-label={`Remove ${n.name}`} onClick={() => setNamed(named.filter((x) => x.name !== n.name))}><X className="h-3 w-3" /></button></span>
              ))}
            </div>
          )}
          <p className="os-faint mt-1 text-[11px]">For a source not on the platform yet. A name that matches a registered source is attached to it automatically; anything else goes to the Data OS Administrator to onboard, and the request reads "Source onboarded" once it is registered, reachable and landed.</p>
        </div>
      )}
      {dupes && (
        <div className="os-banner os-banner-amber text-[12px]" role="alert">
          <b>An open request already asks for this:</b>
          <ul className="mt-1 list-disc pl-5">
            {dupes.map((d) => <li key={d.request_id}><Link className="underline" href={`/os/requests/${d.request_id}`}>{d.title}</Link> ({d.source_name}, {d.stage.toLowerCase()}, by {d.requested_by})</li>)}
          </ul>
          <div className="mt-2"><B variant="warn" disabled={pending} onClick={() => submit(true)} testId="submit-anyway">File it anyway</B></div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <B variant="primary" disabled={!ready || pending || previewing} onClick={() => submit(false)} testId="submit-request-button">
          {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <Send className="h-3.5 w-3.5" />} Submit request
        </B>
        {!ready && !previewing && <span className="os-faint text-[11.5px]">{missing}</span>}
      </div>
      {previewing && <Off />}
      {result?.tone === "fail" && !dupes && <Line outcome={result} />}
    </div>
  );
}

/* ---------------------------------------------------------------- one source of a request */

export function ItemControls({ request, item, sources, runs, previewing }: {
  request: RequestDetail; item: RequestItem; previewing: boolean;
  sources: { source_system_id: string; source_system_name: string }[];
  runs: { run_id: string; run_name: string; current_state: string }[];
}) {
  const { pending, outcome, run } = useRequestCall();
  const [sourceId, setSourceId] = useState(item.source_system_id ?? "");
  const [runId, setRunId] = useState(item.run_id ?? runs[0]?.run_id ?? "");
  const may = request.may;
  const off = pending || previewing;
  if (item.removed_at) return null;
  return (
    <div className="flex flex-col items-end gap-1.5" data-testid={`item-controls-${item.item_id.slice(0, 8)}`}>
      {may.link_source && (
        <span className="flex items-center gap-1.5">
          <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} aria-label={`Registered source for ${item.source_name}`} disabled={off}
                  className="os-input w-[180px] py-1 text-[11.5px]">
            <option value="">Not registered</option>
            {sources.map((s) => <option key={s.source_system_id} value={s.source_system_id}>{s.source_system_name}</option>)}
          </select>
          <B disabled={off || (sourceId || null) === item.source_system_id} title="Link the named source to its registration"
             onClick={() => run(() => linkRequestSource(request.request_id, item.item_id, sourceId || null), sourceId ? "Linked to the registration." : "Unlinked.")}>
            <Link2 className="h-3 w-3" /> {sourceId ? "Link source" : "Unlink"}
          </B>
        </span>
      )}
      {may.link_run && (item.source_system_id || request.request_type === "PRODUCT") && (
        <span className="flex items-center gap-1.5">
          {runs.length > 0 ? (
            <select value={runId} onChange={(e) => setRunId(e.target.value)} aria-label={`Run for ${item.source_name}`} disabled={off}
                    className="os-input w-[220px] py-1 text-[11.5px]">
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_name} ({r.current_state.toLowerCase().replace(/_/g, " ")})</option>)}
            </select>
          ) : <span className="os-faint text-[11px]">No run of this source in this domain yet</span>}
          {runs.length > 0 && runId !== item.run_id && (
            <B disabled={off || !runId} onClick={() => run(() => linkRequestRun(request.request_id, item.item_id, runId), "Run linked.")}>
              <Link2 className="h-3 w-3" /> Link run
            </B>
          )}
          {item.run_id && (
            <B disabled={off} title="Unlink this run" onClick={() => run(() => linkRequestRun(request.request_id, item.item_id, null), "Run unlinked.")}>
              <Unlink className="h-3 w-3" />
            </B>
          )}
        </span>
      )}
      {may.remove_source && (
        <B disabled={off} title="Take this source off the request (kept in the history)"
           onClick={() => run(() => removeRequestSource(request.request_id, item.item_id), "Removed.")}>
          <Trash2 className="h-3 w-3" /> Remove
        </B>
      )}
      {previewing && <Off />}
      <Line outcome={outcome} />
    </div>
  );
}

/* ---------------------------------------------------------------- the request as a whole */

export function AddSource({ requestId, sources, previewing }: { requestId: string; previewing: boolean; sources: { source_system_id: string; source_system_name: string }[] }) {
  const { pending, outcome, run } = useRequestCall();
  const [pick, setPick] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const off = pending || previewing;
  return (
    <div data-testid="add-source">
      <div className="flex flex-wrap gap-2">
        <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Registered source to add" disabled={off} className="os-input w-auto min-w-[180px]">
          <option value="">A registered source...</option>
          {sources.map((s) => <option key={s.source_system_id} value={s.source_system_id}>{s.source_system_name}</option>)}
        </select>
        <input value={name} onChange={(e) => setName(e.target.value)} disabled={off || !!pick} placeholder="...or name one not registered" aria-label="Unregistered source to add"
               className="os-input min-w-[160px] flex-1" />
        <input value={note} onChange={(e) => setNote(e.target.value)} disabled={off} placeholder="What is needed (optional)" aria-label="What is needed" className="os-input min-w-[160px] flex-1" />
        <B disabled={off || (!pick && !name.trim())} onClick={() => run(() => addRequestSource(requestId, pick ? { source_system_id: pick, note: note || undefined } : { source_name: name.trim(), note: note || undefined }),
          "Source added.", () => { setPick(""); setName(""); setNote(""); })}>
          <Plus className="h-3.5 w-3.5" /> Add source
        </B>
      </div>
      {previewing && <Off />}
      <Line outcome={outcome} />
    </div>
  );
}

export function LinkDomain({ requestId, domains, previewing }: { requestId: string; previewing: boolean; domains: { domain_id: string; domain_name: string }[] }) {
  const { pending, outcome, run } = useRequestCall();
  const [pick, setPick] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="link-domain">
      <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Domain to link" disabled={pending || previewing} className="os-input w-auto min-w-[200px]">
        <option value="">The domain, once created...</option>
        {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
      </select>
      <B disabled={!pick || pending || previewing} onClick={() => run(() => linkRequestDomain(requestId, pick), "Domain linked.")}><Link2 className="h-3.5 w-3.5" /> Link domain</B>
      {previewing && <Off />}
      <Line outcome={outcome} />
    </div>
  );
}

export function RequestControls({ request, previewing }: { request: RequestDetail; previewing: boolean }) {
  const { pending, outcome, run } = useRequestCall();
  const [note, setNote] = useState("");
  const may = request.may;
  const off = pending || previewing;
  const reason = note.trim().length >= 3;
  return (
    <div data-testid="request-controls">
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={4000} disabled={previewing} aria-label="Comment or reason"
                placeholder={may.reject ? "A comment, or the reason when you cancel or reject" : "A comment for the people working on this"} className="os-input resize-y" />
      <div className="mt-2 flex flex-wrap gap-2">
        <B disabled={off || !reason} onClick={() => run(() => commentRequest(request.request_id, note.trim()), "Comment added.", () => setNote(""))} testId="request-comment">
          <MessageSquare className="h-3.5 w-3.5" /> Comment
        </B>
        {may.cancel && <B variant="warn" disabled={off} onClick={() => run(() => cancelRequest(request.request_id, note.trim()), "Request cancelled.")} testId="request-cancel"><Ban className="h-3.5 w-3.5" /> Cancel request</B>}
        {may.reject && <B variant="red" disabled={off || !reason} title={reason ? undefined : "Write the reason first: the requester sees it"}
                          onClick={() => run(() => rejectRequest(request.request_id, note.trim()), "Request rejected.")} testId="request-reject"><X className="h-3.5 w-3.5" /> Reject</B>}
        {may.reopen && <B variant="mint" disabled={off} onClick={() => run(() => reopenRequest(request.request_id, note.trim()), "Request reopened.")} testId="request-reopen"><RotateCcw className="h-3.5 w-3.5" /> Reopen</B>}
        {!may.cancel && !may.reject && !may.reopen && <span className="os-faint inline-flex items-center gap-1 text-[11.5px]"><Check className="h-3 w-3" /> Anyone who can see the request may comment.</span>}
      </div>
      {previewing && <Off />}
      <Line outcome={outcome} />
    </div>
  );
}
