"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { ArrowRight, BadgeCheck, ChevronDown, CircleCheck, CircleDashed, FileText, Gauge, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Drawer } from "@/components/hub/overlay";
import { MarkdownDoc } from "@/components/markdown-doc";
import { prettyType } from "../../knowledge/knowledge-ui";
import { verifyKnowledge } from "../../knowledge/actions";
import { domainContract, type DomainContract, type DomainReadiness, type FlowStep, type DomainProvenance } from "../actions";

const STATE: Record<string, { label: string; bar: string; chip: string }> = {
  ok: { label: "Trusted", bar: "bg-emerald-500", chip: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
  partial: { label: "Partly", bar: "bg-amber-500", chip: "bg-amber-500/10 text-amber-700 dark:text-amber-400" },
  missing: { label: "Missing", bar: "bg-rose-500", chip: "bg-rose-500/10 text-rose-700 dark:text-rose-400" },
  none: { label: "Not used", bar: "bg-muted-foreground/30", chip: "bg-muted text-muted-foreground" },
};

function Card({ step, onContract, emphasis }: { step: FlowStep; onContract?: () => void; emphasis?: boolean }) {
  const [all, setAll] = useState(false);
  const s = STATE[step.state] ?? STATE.none;
  const items = all ? step.items : step.items.slice(0, 4);
  return (
    <article className={cn("surface relative w-60 shrink-0 overflow-hidden p-3", emphasis && "ring-1 ring-primary/30")} data-step={step.id} aria-label={step.title}>
      <span className={cn("absolute inset-x-0 top-0 h-1", s.bar)} />
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold leading-tight">{step.title}</h4>
          <p className="text-[11px] text-muted-foreground">{step.role}</p>
        </div>
        <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", s.chip)}>{s.label}</span>
      </div>
      <ul className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">{step.facts.map((f) => <li key={f}>{f}</li>)}</ul>
      {!!items.length && (
        <ul className="mt-2 space-y-1 border-t pt-2 text-[11px]">
          {items.map((it, k) => (
            <li key={k} className="flex items-center gap-1.5">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", (STATE[it.state ?? "ok"] ?? STATE.ok).bar)} />
              {it.kind === "contract" && onContract
                ? <button type="button" onClick={onContract} className="truncate text-left font-medium text-primary hover:underline">{it.label}</button>
                : it.href ? <Link href={it.href} className="truncate hover:text-primary" title={it.label}>{it.label}</Link>
                  : <span className="truncate font-mono" title={it.label}>{it.label}</span>}
            </li>
          ))}
        </ul>
      )}
      {step.items.length > 4 && (
        <button type="button" onClick={() => setAll(!all)} className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-primary">
          {all ? "Show fewer" : `+${step.items.length - 4} more`}<ChevronDown className={cn("h-3 w-3", all && "rotate-180")} />
        </button>
      )}
      {step.id === "model" && onContract && (
        <button type="button" onClick={onContract} className="mt-2 inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium hover:bg-muted">
          <FileText className="h-3 w-3" /> View the contract
        </button>
      )}
    </article>
  );
}

const Arrow = () => <ArrowRight className="mx-1 h-4 w-4 shrink-0 self-center text-muted-foreground" aria-hidden />;

const LEVEL_NAME: Record<DomainReadiness["level"], string> = { none: "Not ready", minimum: "Minimum", recommended: "Recommended", best: "Best" };

/** How ready the domain is for development: three levels, each item met or missing, with where it is fixed. The
 *  better the context a domain brings, the less rework every run needs. */
export function Readiness({ readiness }: { readiness: DomainReadiness }) {
  const [open, setOpen] = useState(readiness.level !== "best");
  const n = readiness.next;
  return (
    <section className="surface p-4" data-testid="domain-readiness">
      <div className="flex flex-wrap items-center gap-3">
        <Gauge className="h-5 w-5 text-primary" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">Readiness: <span className={cn(readiness.level === "none" ? "text-amber-600" : "text-emerald-600")}>{LEVEL_NAME[readiness.level]}</span></h3>
          <p className="text-xs text-muted-foreground">
            {n ? <>Next: <Link href={n.href} className="font-medium text-primary hover:underline">{n.label}</Link> ({n.detail})</> : "Every item is met."}
          </p>
        </div>
        <ol className="flex items-center gap-1.5 text-[11px]">
          {readiness.levels.map((l, i) => (
            <Fragment key={l.id}>
              {i > 0 && <span aria-hidden className="h-px w-4 bg-border" />}
              <li className={cn("rounded-full px-2 py-0.5 font-medium ring-1 ring-inset",
                l.met ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20 dark:text-emerald-300" : "bg-muted text-muted-foreground ring-border")}
                  title={l.meaning}>{l.title} {l.done}/{l.items.length}</li>
            </Fragment>
          ))}
        </ol>
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label="Show the checklist">
          <ChevronDown className={cn("h-4 w-4 transition", open && "rotate-180")} />
        </button>
      </div>
      {open && (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {readiness.levels.map((l) => (
            <div key={l.id} className="rounded-xl border p-3">
              <p className="text-xs font-semibold">{l.title}</p>
              <p className="mb-2 text-[11px] text-muted-foreground">{l.meaning}</p>
              <ul className="space-y-1.5">
                {l.items.map((i) => (
                  <li key={i.id} className="flex gap-2 text-xs">
                    {i.met ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <CircleDashed className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    <span className="min-w-0">
                      <span className={cn("font-medium", i.met && "text-muted-foreground")}>{i.label}</span>
                      <span className="block text-[11px] text-muted-foreground">{i.detail}</span>
                      {!i.met && <Link href={i.href} className="text-[11px] text-primary hover:underline">{i.action}</Link>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** The domain's development lifecycle (as in the team's process diagram): existing tables flow through the data model
 *  and knowledge base to Silver modeling, mapping and code; new tables come from Jira stories or spreadsheets through
 *  SnapLogic ingestion. Each step says how far it can be trusted. */
export function LifecycleFlow({ flow, domainName, onContract }: { flow: DomainProvenance["flow"]; domainName: string; onContract: () => void }) {
  return (
    <section className="space-y-3" data-testid="lifecycle-flow" aria-label="Development lifecycle">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-sm font-semibold">How {domainName} is developed</h3>
        <p className="text-xs text-muted-foreground">Each step shows what it holds and how far it can be trusted. Wrong knowledge upstream means wrong models, mappings and code downstream.</p>
        <span className="ml-auto flex gap-2 text-[11px]">{["ok", "partial", "missing", "none"].map((k) => <span key={k} className="inline-flex items-center gap-1"><span className={cn("h-2 w-2 rounded-full", STATE[k].bar)} />{STATE[k].label}</span>)}</span>
      </div>
      {flow.lanes.map((lane) => (
        <div key={lane.id} className="overflow-x-auto pb-2">
          <div className="flex items-stretch">
            <Card step={{ id: lane.id, title: lane.title, role: lane.role, facts: lane.facts, items: lane.items, state: lane.state }} emphasis />
            {lane.steps.map((st) => (
              <Fragment key={st.id}><Arrow /><Card step={st} onContract={st.id === "model" ? onContract : undefined} /></Fragment>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

/** The contract as the platform reads it: the Markdown file, and what is extracted from it. */
export function ContractDrawer({ domainId, open, onClose }: { domainId: string; open: boolean; onClose: () => void }) {
  const [data, setData] = useState<DomainContract | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open || data) return;
    void domainContract(domainId).then((r) => (r.ok ? setData(r.data) : setError(r.error)));
  }, [open, data, domainId]);
  const tables = new Set((data?.extracted.targets ?? []).map((t) => t.table));
  return (
    <Drawer open={open} onClose={onClose} wide title="Domain contract"
            subtitle={data?.path ? `${data.file} · ${data.format} · from the ${data.where}${data.skill ? ` (${data.skill.name} v${data.skill.version})` : ""}` : "Loading"}>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!data && !error && <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the contract…</p>}
      {data && !data.text && <p className="text-sm text-muted-foreground">{data.detail ?? "The contract could not be read."}</p>}
      {data?.text && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_260px]">
          <article data-testid="contract-text"><MarkdownDoc text={data.text} highlight={(h) => [...tables].some((t) => h.includes(t))} /></article>
          <aside className="space-y-3 text-xs lg:sticky lg:top-0 lg:self-start">
            <section className="rounded-xl border p-3">
              <h4 className="mb-1 font-semibold">What the platform reads from it</h4>
              <p className="text-muted-foreground">Sections marked on the left are parsed into the domain&apos;s tables, keys and mapping rules.</p>
              <dl className="mt-2 space-y-1">
                <div className="flex gap-2"><dt className="text-muted-foreground">Silver</dt><dd className="ml-auto font-mono">{data.extracted.database}.{data.extracted.schema}</dd></div>
                <div className="flex gap-2"><dt className="text-muted-foreground">Hub</dt><dd className="ml-auto font-mono">{data.extracted.hub}</dd></div>
              </dl>
            </section>
            <section className="rounded-xl border p-3">
              <h4 className="mb-1 font-semibold">Tables ({data.extracted.targets.length})</h4>
              <ul className="space-y-1.5">
                {data.extracted.targets.map((t) => (
                  <li key={t.table}>
                    <a href={`#md-${t.table.toLowerCase()}`} className="font-mono font-medium hover:text-primary">{t.table}</a>
                    <span className="text-muted-foreground"> · {t.columns} columns</span>
                    {!!t.hkey_columns.length && <p className="text-muted-foreground">Key: {t.hkey_columns.slice(0, 4).join(", ")}{t.hkey_columns.length > 4 ? "…" : ""}</p>}
                  </li>
                ))}
              </ul>
            </section>
            {!!data.extracted.required.length && (
              <section className="rounded-xl border p-3">
                <h4 className="mb-1 font-semibold">Required mapping keys</h4>
                <ul className="list-disc space-y-0.5 pl-4">{data.extracted.required.map((r) => <li key={r}>{r}</li>)}</ul>
              </section>
            )}
          </aside>
        </div>
      )}
    </Drawer>
  );
}

/** What the stages rely on that no person has checked yet, most used first; a steward verifies each in place. */
export function NeedsVerification({ items, proposed }: { items: DomainProvenance["needs_verification"]; proposed: number }) {
  const [done, setDone] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const verify = async (id: string) => {
    setBusy(id); setError(null);
    const r = await verifyKnowledge(id).catch(() => null);
    setBusy(null);
    if (r?.ok) setDone((s) => new Set(s).add(id)); else setError(r && !r.ok ? r.error : "Could not verify.");
  };
  const left = items.filter((i) => !done.has(i.knowledge_id));
  return (
    <section className="surface space-y-2 p-4" aria-label="Needs verification" data-testid="needs-verification">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold">Needs verification</h3>
        <span className="text-xs text-muted-foreground">{left.length} shown{proposed ? ` · ${proposed} proposed items in the ` : ""}{proposed ? <Link href="/knowledge?tab=inbox" className="text-primary hover:underline">review inbox</Link> : null}</span>
      </div>
      <p className="text-xs text-muted-foreground">Knowledge the stages use that no person has confirmed. Verify what is right; open the item to correct what is not.</p>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {!left.length ? <p className="text-xs text-muted-foreground"><BadgeCheck className="mr-1 inline h-3.5 w-3.5 text-emerald-600" />Everything shown has been verified.</p> : (
        <ul className="max-h-96 divide-y divide-border/60 overflow-y-auto">
          {left.map((i) => (
            <li key={i.knowledge_id} className="flex items-center gap-2 py-1.5 text-xs">
              <div className="min-w-0 flex-1">
                <Link href={`/knowledge?${new URLSearchParams({ tab: "items", q: i.title })}`} className="block truncate font-medium hover:text-primary">{i.title}</Link>
                <span className="text-muted-foreground">{prettyType(i.type)} · {i.source}{i.uses ? ` · used ${i.uses} times in 90 days` : " · not used recently"}</span>
              </div>
              <button type="button" onClick={() => void verify(i.knowledge_id)} disabled={busy === i.knowledge_id}
                      className="inline-flex items-center gap-1 rounded-md border px-2 py-1 font-medium hover:bg-muted disabled:opacity-50">
                {busy === i.knowledge_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <BadgeCheck className="h-3 w-3" />} Verify
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
