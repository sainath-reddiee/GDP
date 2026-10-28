"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import { Ban, Check, Link2, Loader2, MessageSquare, Play, Plus, RotateCcw, Send, Trash2, Unlink, X } from "lucide-react";
import {
  addRequestSource, cancelRequest, commentRequest, createRequest, deleteRequestAction, deleteRunsAction, linkRequestDomain, linkRequestRun, linkRequestSource, rejectRequest,
  removeRequestSource, reopenRequest, startEnhancementRun, startSourceRunForRequest, uploadRequestFiles, type NewRequestInput, type RequestResult,
} from "@/app/os/requests/actions";
import { FilePicker, formOf } from "@/components/os/request-files";
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

export function LiveWorkflowRefresh({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), 4000);
    return () => clearInterval(t);
  }, [active, router]);
  return null;
}

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

/** One-click button to start and link a pipeline run for a request item whose source is already registered and landed. */
export function StartSourceRunButton({
  requestId,
  itemId,
  sourceName,
  sourceType,
  databaseName,
  schemaName,
  domainId,
  domainName,
  note,
  previewing = false,
}: {
  requestId: string;
  itemId: string;
  sourceName: string;
  sourceType?: string | null;
  databaseName?: string | null;
  schemaName?: string | null;
  domainId?: string | null;
  domainName?: string | null;
  note?: string | null;
  previewing?: boolean;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [err, setErr] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-col items-end">
      <button
        type="button"
        disabled={pending || previewing}
        data-testid={`start-source-run-${itemId.slice(0, 8)}`}
        onClick={() =>
          start(async () => {
            setErr(null);
            const res = await startSourceRunForRequest({
              requestId,
              itemId,
              sourceName,
              sourceType,
              databaseName,
              schemaName,
              domainId,
              domainName,
              note,
            });
            if (!res.ok) {
              setErr(res.error);
              return;
            }
            router.refresh();
          })
        }
        className="os-btn os-btn-primary"
      >
        {pending ? <Loader2 className="os-spin h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />} Start run
      </button>
      {err && <span className="mt-1 text-[11px] text-[var(--os-red-ink)]">{err}</span>}
    </span>
  );
}

/* ---------------------------------------------------------------- filing a request (Intake) */

export function SubmitRequest({ type, intent = "NEW", domainId, domainName, picked, availableSources = [], previewing }: {
  type: "SOURCE" | "DOMAIN" | "PRODUCT"; intent?: "NEW" | "ENHANCE"; domainId: string | null; domainName: string | null;
  picked: { source_system_id: string; source_name: string }[];
  availableSources?: { source_system_id: string; source_name: string }[];
  previewing: boolean;
}) {
  const enhance = intent === "ENHANCE";
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [outcome, setOutcomeText] = useState("");
  const [priority, setPriority] = useState<"LOW" | "MEDIUM" | "HIGH">("MEDIUM");
  const [neededBy, setNeededBy] = useState("");
  const [proposed, setProposed] = useState("");
  const [taggedIds, setTaggedIds] = useState<string[]>(() => picked.map((p) => p.source_system_id));
  const [named, setNamed] = useState<{ name: string; note: string }[]>([]);
  const [draft, setDraft] = useState({ name: "", note: "" });
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const { pending, outcome: result, run } = useRequestCall();

  const toggleTagged = (id: string) =>
    setTaggedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const effectiveNamed = [
    ...named,
    ...(draft.name.trim() && !named.some((n) => n.name.toUpperCase() === draft.name.trim().toUpperCase())
      ? [{ name: draft.name.trim(), note: draft.note.trim() }]
      : []),
  ];
  const sources = [
    ...taggedIds.map((id) => ({ source_system_id: id })),
    ...effectiveNamed.map((n) => ({ source_name: n.name, note: n.note || undefined })),
  ];
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
      // the files go up once the request exists; when they do not, the request page says so and takes them again
      if (r.ok && files.length) {
        const up = await uploadRequestFiles(r.data.request_id, formOf(files));
        const kept = up.ok ? up.files.filter((f) => f.file_id).length : 0;
        if (kept < files.length) {
          const why = up.ok ? up.files.filter((f) => !f.file_id).map((f) => `${f.file_name}: ${f.error ?? f.note ?? "not kept"}`).join("; ") : up.error;
          router.push(`/os/requests/${r.data.request_id}?${new URLSearchParams({ files: `${kept} of ${files.length} files attached. ${why}`.slice(0, 400) })}`);
          return { ok: false, error: `Request filed, but ${files.length - kept} file(s) were not attached: ${why}. Attach them on the request.` };
        }
      }
      return r;
    }, files.length ? "Request filed with its files. Routing it now..." : "Request filed. Routing it now...",
    (r) => router.push(`/os/requests/${r.data.request_id}`));
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
      {type === "DOMAIN" && <FilePicker files={files} onChange={setFiles} disabled={previewing || pending} />}
      {type === "DOMAIN" && availableSources.length > 0 && (
        <div data-testid="domain-tag-sources">
          <label className="os-label" htmlFor="rq-tag-source">Tag required sources on the platform (optional)</label>
          <select
            id="rq-tag-source"
            value=""
            disabled={previewing}
            onChange={(e) => {
              const id = e.target.value;
              if (id && !taggedIds.includes(id)) setTaggedIds([...taggedIds, id]);
            }}
            className="os-input"
          >
            <option value="">Choose a registered source to tag...</option>
            {availableSources
              .filter((s) => !taggedIds.includes(s.source_system_id))
              .map((s) => (
                <option key={s.source_system_id} value={s.source_system_id}>{s.source_name}</option>
              ))}
          </select>
          {taggedIds.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {taggedIds.map((id) => {
                const name = availableSources.find((s) => s.source_system_id === id)?.source_name ?? id;
                return (
                  <span key={id} className="os-tag os-tag-green inline-flex items-center gap-1">
                    {name}
                    <button type="button" aria-label={`Remove ${name}`} onClick={() => toggleTagged(id)}>
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}
      {type !== "PRODUCT" && !(type === "SOURCE" && enhance) && (
        <div>
          <span className="os-label">{type === "DOMAIN" ? "Or request a new source not on the platform yet (optional)" : "Request a new source"}</span>
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
    <div className="flex flex-wrap items-center justify-between gap-2 text-[11.5px]" data-testid={`item-controls-${item.item_id.slice(0, 8)}`}>
      <div className="flex flex-wrap items-center gap-2">
        {may.link_source && (
          <span className="inline-flex items-center gap-1.5">
            <span className="os-faint font-semibold">Source:</span>
            <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} aria-label={`Registered source for ${item.source_name}`} disabled={off}
                    className="os-input w-auto min-w-[150px] py-1 text-[11.5px]">
              <option value="">Not registered</option>
              {sources.map((s) => <option key={s.source_system_id} value={s.source_system_id}>{s.source_system_name}</option>)}
            </select>
            {(sourceId || null) !== item.source_system_id && (
              <B disabled={off} title="Link the named source to its registration"
                 onClick={() => run(() => linkRequestSource(request.request_id, item.item_id, sourceId || null), sourceId ? "Linked to the registration." : "Unlinked.")}>
                <Link2 className="h-3 w-3" /> {sourceId ? "Save link" : "Unlink"}
              </B>
            )}
          </span>
        )}
        {may.link_run && (item.source_system_id || request.request_type === "PRODUCT") && runs.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <span className="os-faint font-semibold">Run:</span>
            <select value={runId} onChange={(e) => setRunId(e.target.value)} aria-label={`Run for ${item.source_name}`} disabled={off}
                    className="os-input w-auto min-w-[180px] py-1 text-[11.5px]">
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_name} ({r.current_state.toLowerCase().replace(/_/g, " ")})</option>)}
            </select>
            {runId !== item.run_id && (
              <B disabled={off || !runId} onClick={() => run(() => linkRequestRun(request.request_id, item.item_id, runId), "Run linked.")}>
                <Link2 className="h-3 w-3" /> Link run
              </B>
            )}
            {item.run_id && (
              <>
                <B disabled={off} title="Unlink this run" onClick={() => run(() => linkRequestRun(request.request_id, item.item_id, null), "Run unlinked.")}>
                  <Unlink className="h-3 w-3" /> Unlink
                </B>
                <DeleteRunButton runIds={[item.run_id]} label="Delete run" compact previewing={previewing} />
              </>
            )}
          </span>
        )}
      </div>
      {may.remove_source && (
        <B disabled={off} title="Take this source off the request (kept in the history)"
           onClick={() => run(() => removeRequestSource(request.request_id, item.item_id), "Removed.")}>
          <Trash2 className="h-3 w-3" /> Remove source
        </B>
      )}
      {previewing && <Off />}
      <Line outcome={outcome} />
    </div>
  );
}

/** Delete one or more pipeline runs from any card, board, or workspace. */
const ADMIN_PERSONAS = new Set(["admin", "super"]);

function readActivePersona(): string | null {
  if (typeof document === "undefined") return null;
  const shellPersona = document.querySelector(".ados")?.getAttribute("data-persona");
  if (shellPersona) return shellPersona;
  const preview = document.cookie.match(/(?:^|;\s*)ados_preview_persona=([^;]+)/);
  if (preview) return decodeURIComponent(preview[1]);
  const persona = document.cookie.match(/(?:^|;\s*)ados_persona=([^;]+)/);
  if (persona) return decodeURIComponent(persona[1]);
  return null;
}

function useIsAdminPersona(): boolean {
  const [isAdmin, setIsAdmin] = useState<boolean>(false);
  useEffect(() => {
    const check = () => {
      const p = readActivePersona();
      setIsAdmin(p ? ADMIN_PERSONAS.has(p) : false);
    };
    check();
    const root = document.querySelector(".ados");
    if (!root) return;
    const obs = new MutationObserver(check);
    obs.observe(root, { attributes: true, attributeFilter: ["data-persona"] });
    return () => obs.disconnect();
  }, []);
  return isAdmin;
}

/** Delete one or more pipeline runs directly from any UI card or workspace. Admin only. */
export function DeleteRunButton({
  runIds,
  label = "Delete",
  compact = false,
  iconOnly = false,
  redirectTo,
  previewing = false,
}: {
  runIds: string[];
  label?: string;
  compact?: boolean;
  iconOnly?: boolean;
  redirectTo?: string;
  previewing?: boolean;
}) {
  const router = useRouter();
  const isAdmin = useIsAdminPersona();
  const [confirming, setConfirming] = useState(false);
  const [pending, start] = useTransition();
  const [err, setErr] = useState<string | null>(null);
  if (!isAdmin || previewing || !runIds.length) return null;

  if (confirming) {
    return (
      <span className="inline-flex items-center gap-1" data-testid="delete-run-confirm" data-admin-only="true">
        <button
          type="button"
          disabled={pending || previewing}
          onClick={() =>
            start(async () => {
              setErr(null);
              const res = await deleteRunsAction(runIds);
              if (!res.ok) {
                setErr(res.error);
                return;
              }
              setConfirming(false);
              if (redirectTo) router.push(redirectTo);
              else router.refresh();
            })
          }
          className={cn("os-btn os-btn-red", compact ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-[11.5px]")}
        >
          {pending ? <Loader2 className="os-spin h-3 w-3" /> : <Trash2 className="h-3 w-3" />} Confirm
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => { setConfirming(false); setErr(null); }}
          className={cn("os-btn", compact ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-1 text-[11.5px]")}
        >
          Cancel
        </button>
        {err && <span className="text-[11px] text-[var(--os-red-ink)]">{err}</span>}
      </span>
    );
  }

  return (
    <button
      type="button"
      disabled={previewing}
      onClick={() => setConfirming(true)}
      title={runIds.length > 1 ? `Delete ${runIds.length} runs` : "Delete run"}
      aria-label={runIds.length > 1 ? `Delete ${runIds.length} runs` : label}
      data-testid="delete-run-button"
      data-admin-only="true"
      className={cn(
        "os-btn text-[var(--os-muted)] hover:border-[var(--os-red-line)] hover:bg-[var(--os-red-bg)] hover:text-[var(--os-red-ink)]",
        iconOnly ? "px-2 py-1.5" : compact ? "px-2 py-1 text-[11px]" : "",
      )}
    >
      <Trash2 className="h-3.5 w-3.5" />
      {!iconOnly && <span>{label}</span>}
    </button>
  );
}

/** Delete an onboarding request (and its linked runs) from any card or request screen. Admin only. */
export function DeleteRequestButton({
  requestId,
  label = "Delete",
  iconOnly = false,
  redirectTo,
  previewing = false,
}: {
  requestId: string;
  label?: string;
  iconOnly?: boolean;
  redirectTo?: string;
  previewing?: boolean;
}) {
  const router = useRouter();
  const isAdmin = useIsAdminPersona();
  const [confirming, setConfirming] = useState(false);
  const [pending, start] = useTransition();
  const [err, setErr] = useState<string | null>(null);
  if (!isAdmin || previewing) return null;

  if (confirming) {
    return (
      <span className="inline-flex items-center gap-1" data-testid="delete-request-confirm" data-admin-only="true">
        <button
          type="button"
          disabled={pending || previewing}
          onClick={() =>
            start(async () => {
              setErr(null);
              const res = await deleteRequestAction(requestId, true);
              if (!res.ok) {
                setErr(res.error);
                return;
              }
              setConfirming(false);
              if (redirectTo) router.push(redirectTo);
              else router.refresh();
            })
          }
          className="os-btn os-btn-red px-2.5 py-1 text-[11.5px]"
        >
          {pending ? <Loader2 className="os-spin h-3 w-3" /> : <Trash2 className="h-3 w-3" />} Confirm
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => { setConfirming(false); setErr(null); }}
          className="os-btn px-2 py-1 text-[11.5px]"
        >
          Cancel
        </button>
        {err && <span className="text-[11px] text-[var(--os-red-ink)]">{err}</span>}
      </span>
    );
  }

  return (
    <button
      type="button"
      disabled={previewing}
      onClick={() => setConfirming(true)}
      title="Delete this workflow request and its linked runs"
      aria-label={label}
      data-testid="delete-request-button"
      data-admin-only="true"
      className={cn(
        "os-btn text-[var(--os-muted)] hover:border-[var(--os-red-line)] hover:bg-[var(--os-red-bg)] hover:text-[var(--os-red-ink)]",
        iconOnly && "px-2 py-1.5",
      )}
    >
      <Trash2 className="h-3.5 w-3.5" />
      {!iconOnly && <span>{label}</span>}
    </button>
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
