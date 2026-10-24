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

export function SourceIntakeForm({ enhance, domains, sources, runs, initialDomain, initialSources, previewing }: {
  enhance: boolean; domains: IntakeDomain[]; sources: IntakeSource[]; runs: IntakeRun[];
  initialDomain: string | null; initialSources: string[]; previewing: boolean;
}) {
  const router = useRouter();
  const { pending, outcome, run } = useRequestCall();
  /** the same form as Enhance (or New), keeping the domain and the sources picked */
  const switchHref = (toEnhance: boolean, domainId: string | null, sourceIds: string[]) => {
    const q = new URLSearchParams({ type: "source", intent: toEnhance ? "enhance" : "new", ...(domainId ? { domain: domainId } : {}) });
    for (const id of sourceIds) q.append("source", id);
    return `/os/intake/new?${q}`;
  };
  const [title, setTitle] = useState("");
  const [outcomeText, setOutcomeText] = useState("");
  const [priority, setPriority] = useState<"LOW" | "MEDIUM" | "HIGH">("MEDIUM");
  const [neededBy, setNeededBy] = useState("");
  const [domainId, setDomainId] = useState(initialDomain ?? "");
  const [picked, setPicked] = useState<string[]>(initialSources);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [named, setNamed] = useState<{ name: string; note: string }[]>([]);
  const [draft, setDraft] = useState({ name: "", note: "" });
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);

  const domain = domains.find((d) => d.domain_id === domainId) ?? null;
  const feedsHere = (s: IntakeSource) => !!domain && s.feeds.some((f) => f.domain === domain.domain_name);
  // the sources grouped by the domains they feed (a source feeding several is under each); the chosen domain first
  const groups = useMemo(() => {
    const by = new Map<string, IntakeSource[]>();
    for (const s of sources) {
      const keys = s.feeds.length ? s.feeds.map((f) => f.domain) : [NO_DOMAIN];
      for (const k of keys) by.set(k, [...(by.get(k) ?? []), s]);
    }
    return [...by.entries()]
      .map(([name, list]) => ({ name, list: list.sort((a, b) => Number(b.ready) - Number(a.ready) || a.name.localeCompare(b.name)) }))
      .sort((a, b) => Number(b.name === domain?.domain_name) - Number(a.name === domain?.domain_name)
        || Number(a.name === NO_DOMAIN) - Number(b.name === NO_DOMAIN) || a.name.localeCompare(b.name));
  }, [sources, domain?.domain_name]);
  const mine = groups.find((g) => g.name === domain?.domain_name);
  const others = groups.filter((g) => g !== mine);

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  // Enhance widens a source already feeding the domain: only those can be picked
  const usable = picked.filter((id) => !enhance || sources.some((s) => s.id === id && feedsHere(s)));
  const chosen = sources.filter((s) => usable.includes(s.id));
  const alreadyHere = !enhance ? chosen.filter(feedsHere) : [];
  const notLanded = chosen.filter((s) => !s.ready);
  const related = runs.filter((r) => (!domain || r.domain_name === domain.domain_name)
    && (!chosen.length || chosen.some((s) => s.name === r.source_system_name))).slice(0, 6);

  const sourceCount = usable.length + (enhance ? 0 : named.length);
  const missing = !title.trim() ? "Give the request a title." : !domain ? "Choose the domain."
    : !sourceCount ? (enhance ? "Pick the source to enhance." : "Pick or name at least one source.")
    : enhance && usable.some((id) => !(notes[id] ?? "").trim()) ? "Say what to add for each source." : "";

  const submit = (allowDuplicate = false) => {
    const body: NewRequestInput = {
      request_type: "SOURCE", intent: enhance ? "ENHANCE" : "NEW", title: title.trim(), business_outcome: outcomeText.trim() || undefined,
      priority, needed_by: neededBy || null, domain_id: domain?.domain_id ?? null, proposed_domain: null, allow_duplicate: allowDuplicate,
      sources: [
        ...usable.map((id) => ({ source_system_id: id, note: (notes[id] ?? "").trim() || undefined })),
        ...(enhance ? [] : named.map((n) => ({ source_name: n.name, note: n.note || undefined }))),
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
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]" data-testid="submit-request">
      <div className="flex min-w-0 flex-col gap-4">
        {/* 1. what is asked, and why */}
        <section className="os-card os-rise p-5" aria-labelledby="rq-details">
          <h2 id="rq-details" className="text-[14.5px] font-extrabold">Request details</h2>
          <p className="os-muted mt-0.5 text-[12px]">{enhance ? "What the business needs added from a source already onboarded" : "What the business needs from a source"}</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
            <div><label className="os-label" htmlFor="rq-title">Request title</label>
              <input id="rq-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} disabled={previewing}
                     placeholder={enhance ? "e.g. Salesforce: add campaign tables" : `e.g. ${domain?.domain_name ?? "Opportunity"} from Salesforce`} className="os-input" /></div>
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

        {/* 2. where it goes, and from which sources */}
        <section className="os-card os-rise p-5" aria-labelledby="rq-sources" data-testid="attach-sources">
          <h2 id="rq-sources" className="text-[14.5px] font-extrabold">{enhance ? "Source to enhance" : "Sources"}</h2>
          <p className="os-muted mt-0.5 text-[12px]">
            {enhance ? "Pick the onboarded source and say what to add: tables, columns or changes." : "Sources on the platform, grouped by the domains they already feed. The Data OS Administrator onboards them."}
          </p>
          <div className="mt-3">
            <label className="os-label" htmlFor="domain">Business domain</label>
            <select id="domain" value={domainId} onChange={(e) => setDomainId(e.target.value)} className="os-input">
              <option value="">Choose an onboarded domain...</option>
              {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}{d.description ? ` · ${d.description.slice(0, 60)}` : ""}</option>)}
            </select>
            {domain && <p className="os-faint mt-1.5 text-[11px]"><Layers className="mr-1 inline h-3 w-3" />Reuses the {domain.domain_name} domain: {domain.target_tables} modelled tables and {domain.knowledge_items} memory records.</p>}
          </div>

          {domain && (
            <fieldset className="mt-4" data-testid="sources-feeding">
              <legend className="os-label">Already feeding {domain.domain_name} {mine ? `(${mine.list.length})` : ""}</legend>
              {mine ? (
                <div className="grid gap-2">
                  {mine.list.map((s) => (
                    <div key={s.id}>
                      <SourceCard s={s} picked={usable.includes(s.id)} onToggle={() => toggle(s.id)} domain={domain.domain_name} />
                      {enhance && usable.includes(s.id) && (
                        <input value={notes[s.id] ?? ""} onChange={(e) => setNotes({ ...notes, [s.id]: e.target.value })} maxLength={2000} disabled={previewing}
                               aria-label={`What to add from ${s.name}`} placeholder={`What to add from ${s.name}: tables, columns or changes`}
                               className="os-input mt-1.5 text-[12px]" data-testid={`scope-${s.name}`} />
                      )}
                    </div>
                  ))}
                </div>
              ) : <p className="os-faint text-[11.5px]">No source feeds {domain.domain_name} yet{enhance ? ": there is nothing to enhance, request a New source." : ": pick one below, or request a new one."}</p>}
            </fieldset>
          )}

          {!enhance && (
            <div className="mt-4" data-testid="sources-registered">
              <span className="os-label">{domain ? "Other sources on the platform, by domain" : "Sources on the platform, by domain"}</span>
              {others.length ? (
                <div className="flex flex-col gap-1.5">
                  {others.map((g) => {
                    const n = g.list.filter((s) => usable.includes(s.id)).length;
                    return (
                      <details key={g.name} className="group rounded-lg border border-[var(--os-line-soft)]" open={!domain && others.length <= 2}>
                        <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-[12px] font-bold">
                          <ChevronRight className="h-3.5 w-3.5 transition group-open:rotate-90" />{g.name}
                          <span className="os-faint font-semibold">{g.list.length}</span>
                          {n > 0 && <span className="ml-auto rounded-full bg-[var(--os-green-bg)] px-2 text-[10.5px] text-[var(--os-green-ink)]">{n} picked</span>}
                        </summary>
                        <div className="grid gap-1.5 px-3 pb-3 sm:grid-cols-2">
                          {g.list.map((s) => <SourceCard key={`${g.name}-${s.id}`} s={s} picked={usable.includes(s.id)} onToggle={() => toggle(s.id)} domain={g.name} />)}
                        </div>
                      </details>
                    );
                  })}
                </div>
              ) : <p className="os-faint text-[11.5px]">{sources.length ? "Every source on the platform already feeds this domain." : "No source is registered on the platform yet."}</p>}
            </div>
          )}

          {alreadyHere.length > 0 && domain && (
            <p className="os-banner os-banner-blue mt-3 text-[11.5px]" data-testid="suggest-enhance">
              {alreadyHere.map((s) => s.name).join(", ")} already feed{alreadyHere.length === 1 ? "s" : ""} {domain.domain_name}. Asking for more from {alreadyHere.length === 1 ? "it" : "them"}?{" "}
              <Link className="font-bold underline" href={switchHref(true, domain.domain_id, alreadyHere.map((s) => s.id))}>Enhance instead</Link>: it skips onboarding.
            </p>
          )}

          {!enhance && (
            <div className="mt-4">
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
              <p className="os-faint mt-1 text-[11px]">For a source not on the platform yet. A name that matches a registered source is attached to it automatically.</p>
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
            {!missing && <span className="os-faint text-[11.5px]"><Check className="mr-1 inline h-3 w-3" />{chosen.length + (enhance ? 0 : named.length)} source{chosen.length + named.length === 1 ? "" : "s"} for {domain?.domain_name}</span>}
          </div>
          {previewing && <Off />}
          {outcome?.tone === "fail" && !dupes && <Line outcome={outcome} />}
        </section>
      </div>

      {/* where the request goes, for what is picked now */}
      <aside className="flex min-w-0 flex-col gap-4">
        <section className="os-card os-rise p-5 xl:sticky xl:top-[76px]" data-testid="flow-preview">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[14.5px] font-extrabold">Where this goes</h2>
            <span className={cn("os-tag", enhance ? "os-tag-purple" : "os-tag-blue")}>{enhance ? "Enhance flow" : "New source flow"}</span>
          </div>
          <p className="os-muted mt-0.5 text-[12px]">{enhance ? "The source is already onboarded: only the delta is landed, mapped and rebuilt." : "Routed by the platform, step by step."}</p>
          <ol className="mt-3 flex flex-col gap-2.5">
            {enhance ? (
              <>
                <Step n={1} title="Scope" who="You" note="The source and what to add, on this request" />
                <Step n={2} title="Land the delta" who="Data OS Administrator" skipped={chosen.length > 0 && !notLanded.length}
                      note={notLanded.length ? `${notLanded.map((s) => s.name).join(", ")} must land the added tables first` : "Skipped: the source is already landed"} />
                <Step n={3} title="Impact & delta mapping" who="Data Designer" note="Starts the enhancement run, reuses the approved model and maps only what is added" />
                <Step n={4} title="Regenerate & regression" who="Forward Engineering Lead" note="Regenerates the affected models and validates them against the gate" />
                <Step n={5} title="Review" who="Data Developer" note="Reviews and approves the code change" />
              </>
            ) : (
              <>
                <Step n={1} title="File the request" who="You" note="Domain, sources and the business outcome" />
                <Step n={2} title="Onboard the source" who="Data OS Administrator"
                      note={chosen.length && !notLanded.length && !named.length ? "Already registered and landed: the administrator starts its run in the domain" : "Registers, reaches and lands the source, then starts its run in the domain"} />
                <Step n={3} title="Design" who="Data Designer" note="Profiles, models and maps; approvers decide the mapping, transformations and checks" />
                <Step n={4} title="Build" who="Forward Engineering Lead" note="Generates the code and validates it against the gate" />
                <Step n={5} title="Review" who="Data Developer" note="Reviews and approves the code" />
              </>
            )}
          </ol>
          <p className="os-faint mt-3 border-t border-[var(--os-line)] pt-3 text-[11px]">
            {enhance ? "An enhancement never re-onboards the source; the Data OS Administrator lands only what is added, when it is not landed yet."
              : "Sources are onboarded by the Data OS Administrator: they register the source, land it and start its run, and the request moves on to the Data Designer."}
          </p>
          {domain && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
              {chosen.map((s) => <span key={s.id} className="os-tag os-tag-blue"><Database className="h-3 w-3" />{s.name}</span>)}
              {named.map((n) => <span key={n.name} className="os-tag os-tag-amber">{n.name} (new)</span>)}
              {(chosen.length > 0 || named.length > 0) && <span className="os-faint">feeds</span>}
              <span className="os-tag os-tag-green"><Layers className="h-3 w-3" />{domain.domain_name}</span>
            </div>
          )}
        </section>

        <section className="os-card os-rise p-5" aria-labelledby="rq-related">
          <h2 id="rq-related" className="text-[14px] font-extrabold">Related workflows</h2>
          <p className="os-muted mt-0.5 text-[12px]">{domain || chosen.length ? "Runs that already touch this domain or these sources" : "Pick a domain or a source to see what exists"}</p>
          {related.length && (domain || chosen.length) ? (
            <div className="os-divide mt-2">
              {related.map((r) => (
                <Link key={r.run_id} href={`/os/build?view=selected&run=${r.run_id}`} className="flex items-center justify-between gap-3 py-2 os-hover">
                  <span className="min-w-0"><span className="block truncate text-[12px] font-bold">{r.run_name}</span>
                    <span className="os-faint text-[10.5px]">{r.domain_name ?? "No domain"} · {r.source_system_name ?? "no source"} · {r.updated}</span></span>
                  <span className="os-tag shrink-0">{r.state}</span>
                </Link>
              ))}
            </div>
          ) : <p className="os-faint mt-2 text-[11.5px]">{domain || chosen.length ? "Nothing yet: this would be a first." : "No selection."}</p>}
        </section>
      </aside>
      {/* the same picks as the other intent */}
      <p className="os-faint text-[11px] xl:col-span-2">
        {enhance ? "Need a source that is not onboarded yet? " : "Asking for more from a source already onboarded? "}
        <Link className="font-bold underline" href={switchHref(!enhance, domain?.domain_id ?? null, usable)}>{enhance ? "File a New source request" : "Enhance it instead"}</Link>.
      </p>
    </div>
  );
}
