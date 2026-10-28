"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Check, ChevronRight, Database, Layers, Loader2, Plus, Send, X } from "lucide-react";
import { createRequest, type NewRequestInput } from "@/app/os/requests/actions";
import type { Duplicate } from "@/lib/os/requests";
import { B, Line, Off, useRequestCall } from "./request-ui";
import { cn } from "@/lib/utils";

// The source request form of Intake (/os/intake/new?type=source): the request details first, then the domain and the
// sources already on the platform grouped by the domains they feed, and on the right where the request will go. New
// and Enhance route differently (services/onboarding/requests.py): a new source goes to the Data OS Administrator, then
// the Data Designer; an enhanced one skips the administrator unless what it adds is not landed, and the designer maps
// only the delta. Everything stays on the page while the person picks (no reload loses what they typed).

export type IntakeDomain = { domain_id: string; domain_name: string; description: string | null; target_tables: number; knowledge_items: number };
export type IntakeSource = {
  id: string; name: string; type: string; tables: number | null; ready: boolean; health: string;
  /** the domains its runs feed, by name, with the latest run state in each */
  feeds: { domain: string; runs: number; latest: string }[];
};
export type IntakeRun = { run_id: string; run_name: string; domain_name: string | null; source_system_name: string | null; state: string; updated: string };

const NO_DOMAIN = "Not in a domain yet";

function SourceCard({ s, picked, onToggle, domain }: { s: IntakeSource; picked: boolean; onToggle: () => void; domain: string | null }) {
  const here = domain ? s.feeds.find((f) => f.domain === domain) : undefined;
  return (
    <label className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[12px] transition os-hover",
                         picked ? "border-[var(--os-emerald)] bg-[var(--os-green-bg)]" : "border-[var(--os-line)]")}>
      <input type="checkbox" checked={picked} onChange={onToggle} className="accent-[var(--os-forest)]" aria-label={s.name} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-bold">{s.name}</span>
        <span className="os-faint block truncate text-[10.5px]">
          {here ? `${here.runs} run${here.runs === 1 ? "" : "s"} · latest ${here.latest}` : `${s.type} · ${s.tables ?? "?"} table${s.tables === 1 ? "" : "s"}`}
          {" · "}{s.ready ? "ready" : s.health}
        </span>
      </span>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", s.ready ? "bg-[var(--os-leaf)]" : "bg-[#dfa94b]")}
            title={s.ready ? "Ready: registered, reachable and landed" : s.health} />
    </label>
  );
}

/** One step of the route the request will take. */
function Step({ n, title, who, note, skipped }: { n: number; title: string; who: string; note: string; skipped?: boolean }) {
  return (
    <li className={cn("flex gap-2.5", skipped && "opacity-50")}>
      <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-extrabold",
                          skipped ? "bg-[var(--os-neutral-bg)] os-faint" : "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]")}>
        {skipped ? <X className="h-3 w-3" /> : n}
      </span>
      <span className="min-w-0 text-[12px]">
        <b className={cn(skipped && "line-through")}>{title}</b> <span className="os-faint">· {who}</span>
        <span className="os-faint block text-[11px] leading-snug">{note}</span>
      </span>
    </li>
  );
}

export function SourceIntakeForm({ enhance, domains, sources, runs: _runs, initialDomain, initialSources, previewing }: {
  enhance: boolean; domains: IntakeDomain[]; sources: IntakeSource[]; runs: IntakeRun[];
  initialDomain: string | null; initialSources: string[]; previewing: boolean;
}) {
  const router = useRouter();
  const { pending, outcome, run } = useRequestCall();
  const switchHref = (toEnhance: boolean, sourceIds: string[]) => {
    const q = new URLSearchParams({ type: "source", intent: toEnhance ? "enhance" : "new" });
    for (const id of sourceIds) q.append("source", id);
    return `/os/intake/new?${q}`;
  };
  const [title, setTitle] = useState("");
  const [outcomeText, setOutcomeText] = useState("");
  const [priority, setPriority] = useState<"LOW" | "MEDIUM" | "HIGH">("MEDIUM");
  const [neededBy, setNeededBy] = useState("");
  const [picked, setPicked] = useState<string[]>(initialSources);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [named, setNamed] = useState<{ name: string; note: string }[]>([]);
  const [draft, setDraft] = useState({ name: "", note: "" });
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);

  const usable = enhance ? picked.filter((id) => sources.some((s) => s.id === id)) : [];
  const chosen = sources.filter((s) => usable.includes(s.id));
  const effectiveNamed = useMemo(() => {
    if (enhance) return [];
    const list = [...named];
    const dName = draft.name.trim();
    if (dName && !list.some((n) => n.name.toUpperCase() === dName.toUpperCase())) {
      list.push({ name: dName, note: draft.note.trim() });
    }
    return list;
  }, [enhance, named, draft.name, draft.note]);

  const resolvedDomainId = useMemo(() => {
    if (initialDomain) return initialDomain;
    for (const s of chosen) {
      const feedDomain = s.feeds[0]?.domain;
      if (feedDomain) {
        const match = domains.find((d) => d.domain_name === feedDomain);
        if (match) return match.domain_id;
      }
    }
    return domains.find((d) => d.domain_name === "GENERAL")?.domain_id ?? domains[0]?.domain_id ?? null;
  }, [initialDomain, chosen, domains]);

  const sourceCount = usable.length + effectiveNamed.length;
  const missing = !title.trim() ? "Give the request a title."
    : !sourceCount ? (enhance ? "Pick the source to enhance." : "Enter the source name.")
    : enhance && usable.some((id) => !(notes[id] ?? "").trim()) ? "Say what to add for the selected source." : "";

  const submit = (allowDuplicate = false) => {
    const body: NewRequestInput = {
      request_type: "SOURCE", intent: enhance ? "ENHANCE" : "NEW", title: title.trim(), business_outcome: outcomeText.trim() || undefined,
      priority, needed_by: neededBy || null, domain_id: resolvedDomainId, proposed_domain: null, allow_duplicate: allowDuplicate,
      sources: [
        ...usable.map((id) => ({ source_system_id: id, note: (notes[id] ?? "").trim() || undefined })),
        ...effectiveNamed.map((n) => ({ source_name: n.name, note: n.note || undefined })),
      ],
    };
    setDupes(null);
    run(async () => {
      const r = await createRequest(body);
      if (!r.ok && r.duplicates) setDupes(r.duplicates);
      return r;
    }, "Request filed. Routing it now...", (r) => router.push(`/os/intake?${new URLSearchParams({ view: "selected", request: r.data.request_id })}`));
  };

  return (
    <div className="max-w-3xl" data-testid="submit-request">
      <div className="flex min-w-0 flex-col gap-4">
        {/* 1. what is asked, and why */}
        <section className="os-card os-rise p-5" aria-labelledby="rq-details">
          <h2 id="rq-details" className="text-[14.5px] font-extrabold">Request details</h2>
          <p className="os-muted mt-0.5 text-[12px]">{enhance ? "What the business needs added from a source already onboarded" : "What the business needs from a source"}</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
            <div><label className="os-label" htmlFor="rq-title">Request title</label>
              <input id="rq-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} disabled={previewing}
                     placeholder={enhance ? "e.g. Salesforce: add campaign tables" : "e.g. Opportunity from Salesforce"} className="os-input" /></div>
            <div><label className="os-label" htmlFor="rq-priority">Priority</label>
              <select id="rq-priority" value={priority} onChange={(e) => setPriority(e.target.value as typeof priority)} disabled={previewing} className="os-input">
                <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option>
              </select></div>
            <div><label className="os-label" htmlFor="rq-needed">Needed by</label>
              <input id="rq-needed" type="date" value={neededBy} onChange={(e) => setNeededBy(e.target.value)} disabled={previewing} className="os-input" /></div>
          </div>
          <div className="mt-3"><label className="os-label" htmlFor="rq-outcome">Business outcome</label>
            <textarea id="rq-outcome" value={outcomeText} onChange={(e) => setOutcomeText(e.target.value)} rows={2} maxLength={4000} disabled={previewing}
                      placeholder="What the business gets, the consumers and their SLA" className="os-input resize-y" /></div>
        </section>

        {/* 2. which source */}
        <section className="os-card os-rise p-5" aria-labelledby="rq-sources" data-testid="attach-sources">
          <h2 id="rq-sources" className="text-[14.5px] font-extrabold">{enhance ? "Source to enhance" : "Source to onboard"}</h2>
          <p className="os-muted mt-0.5 text-[12px]">
            {enhance ? "Choose the onboarded source and specify what to add: tables, columns or changes." : "Specify the new source system to onboard and what is needed from it."}
          </p>

          {enhance ? (
            <div className="mt-3 flex flex-col gap-3" data-testid="sources-feeding">
              <div>
                <label className="os-label" htmlFor="source-select">Onboarded source</label>
                <select
                  id="source-select"
                  value={usable[0] ?? ""}
                  onChange={(e) => setPicked(e.target.value ? [e.target.value] : [])}
                  disabled={previewing}
                  className="os-input"
                >
                  <option value="">Choose an onboarded source...</option>
                  {sources.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              {chosen.map((s) => (
                <div key={s.id}>
                  <label className="os-label" htmlFor={`scope-${s.id}`}>What to add from {s.name}</label>
                  <input
                    id={`scope-${s.id}`}
                    value={notes[s.id] ?? ""}
                    onChange={(e) => setNotes({ ...notes, [s.id]: e.target.value })}
                    maxLength={2000}
                    disabled={previewing}
                    aria-label={`What to add from ${s.name}`}
                    placeholder={`Tables, columns or changes needed from ${s.name}`}
                    className="os-input text-[12px]"
                    data-testid={`scope-${s.name}`}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-3">
              <span className="os-label">New source system</span>
              <div className="flex flex-wrap gap-2">
                <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Source name, e.g. Yardi" maxLength={256}
                       aria-label="Unregistered source name" disabled={previewing} className="os-input min-w-[160px] flex-1" />
                <input value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} placeholder="What is needed from it (tables, objects, SLA)" maxLength={2000}
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
              <p className="os-faint mt-1.5 text-[11px]">The Data OS Administrator will register, connect and land this source.</p>
            </div>
          )}
        </section>

        {/* 3. file it */}
        <section className="os-card os-rise p-4">
          {dupes && (
            <div className="os-banner os-banner-amber mb-3 text-[12px]" role="alert">
              <b>An open request already asks for this:</b>
              <ul className="mt-1 list-disc pl-5">
                {dupes.map((d) => <li key={d.request_id}><Link className="underline" href={`/os/intake?view=selected&request=${d.request_id}`}>{d.title}</Link> ({d.source_name}, {d.stage.toLowerCase()}, by {d.requested_by})</li>)}
              </ul>
              <div className="mt-2"><B variant="warn" disabled={pending} onClick={() => submit(true)} testId="submit-anyway">File it anyway</B></div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <B variant="primary" disabled={!!missing || pending || previewing} onClick={() => submit(false)} testId="submit-request-button">
              {pending ? <Loader2 className="os-spin h-3.5 w-3.5" /> : <Send className="h-3.5 w-3.5" />} Submit request
            </B>
            {missing && !previewing && <span className="os-faint text-[11.5px]">{missing}</span>}
            {!missing && <span className="os-faint text-[11.5px]"><Check className="mr-1 inline h-3 w-3" />{sourceCount} source{sourceCount === 1 ? "" : "s"} selected</span>}
          </div>
          {previewing && <Off />}
          {outcome?.tone === "fail" && !dupes && <Line outcome={outcome} />}
        </section>

        <p className="os-faint text-[11px]">
          {enhance ? "Need a source that is not onboarded yet? " : "Asking for more from a source already onboarded? "}
          <Link className="font-bold underline" href={switchHref(!enhance, usable)}>{enhance ? "File a New source request" : "Enhance it instead"}</Link>.
        </p>
      </div>
    </div>
  );
}
