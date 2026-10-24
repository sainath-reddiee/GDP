import Link from "next/link";
import { ArrowRight, BadgeCheck, BookOpen, BookmarkCheck, Clock, Inbox, Search, Sparkles, TrendingUp } from "lucide-react";
import { api } from "@/lib/api";
import { stateLabel } from "@/lib/run-labels";
import { loadDomains, loadKnowledge, loadKnowledgeInbox, loadKnowledgeOverview, loadKnowledgeRecord } from "@/lib/os/data";
import type { FeedItem, InboxItem, KnowledgeItem } from "@/app/knowledge/actions";
import { FeedPanel, HealthPanel, InboxPanel, OverviewPanel } from "@/app/knowledge/knowledge-hub";
import { KnowledgeBrowser } from "@/app/knowledge/knowledge-browser";
import { KnowledgeSearch } from "@/app/knowledge/search-form";
import { DomainsGrid } from "@/app/domains/domains-screen";
import { KnowledgeTools, OpenRecord } from "@/components/os/knowledge-tools";
import { guard } from "@/lib/os/session";
import { ACTIONS, actMode, canSee } from "@/lib/os/access";
import { InboxDecision, VerifyKnowledge } from "@/components/os/decide";
import { Action } from "@/components/os/action";
import { Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Knowledge hub" };

/** Enterprise Memory's knowledge types, in the hub's five families. */
const FAMILIES: { key: string; label: string; types: string[]; tone: Tone }[] = [
  { key: "domain", label: "Domain and business", types: ["GLOSSARY", "MODEL_DEFINITION", "BUSINESS_RULE", "NAMING_STANDARD", "ONBOARDING_GUIDE", "EXAMPLE"], tone: "green" },
  { key: "mapping", label: "Mapping and transformation", types: ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "COLUMN_RULE", "STTM_TEMPLATE"], tone: "blue" },
  { key: "quality", label: "Quality and testing", types: ["SODA_PATTERN", "QA_TEST", "EXCEPTION"], tone: "amber" },
  { key: "engineering", label: "Engineering patterns", types: ["DBT_PATTERN", "AIRFLOW_DAG"], tone: "purple" },
  { key: "operational", label: "Operational learning", types: ["AIRFLOW_RUNBOOK", "INCIDENT_RESOLUTION", "CASE_RESOLUTION"], tone: "red" },
];

/** The hub's views: the catalog with its record, plus the full Enterprise Memory tools. */
const VIEWS = [
  { key: "catalog", label: "Catalog" },
  { key: "domains", label: "Domains" },
  { key: "ask", label: "Ask and insights" },
  { key: "items", label: "All items" },
  { key: "inbox", label: "Review inbox" },
  { key: "feed", label: "Learning feed" },
  { key: "health", label: "Duplicates and stale" },
] as const;
type View = (typeof VIEWS)[number]["key"];
const LIMIT = 50;
type Pair = { a_id: string; a_title: string; a_origin: string; b_id: string; b_title: string; b_origin: string; knowledge_type: string; domain_name: string; score: number };
const LAYER: Record<string, string> = { SILVER: "Silver", GOLD: "Gold", ANY: "Shared" };
const familyOf = (type: string) => FAMILIES.find((f) => f.types.includes(type));

const ORIGIN: Record<string, string> = {
  SEED: "Domain pack", MAPPING: "Mapping reviews", SODA: "Data quality approvals", DBT: "Code generation", REVIEW: "Code review", OPS: "Operations", USER: "People",
};

type Params = { q?: string; family?: string; domain?: string; id?: string; view?: string; tab?: string; deleted?: string;
                type?: string; status?: string; layer?: string; offset?: string; origin?: string; domain_id?: string };

export default async function KnowledgeHub({ searchParams }: { searchParams: Params }) {
  const access = await guard("knowledge");
  const persona = access.persona;
  // ?tab= is the legacy hub's name for the same views
  const view = (VIEWS.find((v) => v.key === (searchParams.view ?? searchParams.tab))?.key ?? "catalog") as View;
  if (view !== "catalog") return <ToolView view={view} sp={searchParams} access={access} />;
  const family = FAMILIES.find((f) => f.key === searchParams.family);
  const params: Record<string, string> = {};
  if (searchParams.q) params.q = searchParams.q;
  if (searchParams.domain) params.domain_id = searchParams.domain;
  const [overview, domains, list, inbox] = await Promise.all([loadKnowledgeOverview(), loadDomains(), loadKnowledge(params), loadKnowledgeInbox()]);
  // curating memory is a steward's decision (KNOWLEDGE.EDIT); others who may raise change requests send it for approval
  const curate = actMode(access, ACTIONS.verifyKnowledge.privilege);
  const items = list.data.items.filter((i) => !family || family.types.includes(i.knowledge_type));
  const selectedId = searchParams.id ?? items[0]?.knowledge_id;
  const record = selectedId ? await loadKnowledgeRecord(selectedId) : null;
  const sel = record?.item ?? items.find((i) => i.knowledge_id === selectedId) ?? null;
  const o = overview.data;
  const href = (patch: Record<string, string | undefined>) => {
    const p = new URLSearchParams();
    const merged: Record<string, string | undefined> = { q: searchParams.q, family: searchParams.family, domain: searchParams.domain, ...patch };
    Object.entries(merged).forEach(([k, v]) => v && p.set(k, v));
    const qs = p.toString();
    return `/os/knowledge${qs ? `?${qs}` : ""}`;
  };
  const familyCount = (f: (typeof FAMILIES)[number]) => f.types.reduce((n, t) => n + (o?.by.type[t] ?? 0), 0);

  return (
    <Frame access={access} o={o} view="catalog" domains={domains.data} error={overview.error ?? list.error}>
      <Card className="mb-4" pad={false}>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-[var(--os-line)] px-5" role="tablist">
          <Link href={href({ family: undefined, id: undefined })} role="tab" aria-selected={!family} className="os-tab">All knowledge <span className="os-faint">{o?.totals.active ?? ""}</span></Link>
          {FAMILIES.map((f) => (
            <Link key={f.key} href={href({ family: f.key, id: undefined })} role="tab" aria-selected={family?.key === f.key} className="os-tab">
              {f.label} <span className="os-faint">{o ? familyCount(f) : ""}</span>
            </Link>
          ))}
        </div>
        <form action="/os/knowledge" className="flex flex-wrap items-center gap-2 px-5 py-3">
          {family && <input type="hidden" name="family" value={family.key} />}
          <div className="relative min-w-[240px] flex-1">
            <Search className="os-faint pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
            <input name="q" defaultValue={searchParams.q} placeholder="Search memory: a column, a rule, a source, a pattern..." className="os-input pl-8" />
          </div>
          <select name="domain" defaultValue={searchParams.domain ?? ""} className="os-input w-auto" aria-label="Domain">
            <option value="">All domains</option>
            {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
          </select>
          <button className="os-btn os-btn-primary" type="submit">Search</button>
        </form>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card title="Structured knowledge catalog" subtitle={`${items.length} of ${list.data.total} record${list.data.total === 1 ? "" : "s"}${searchParams.q ? ` matching "${searchParams.q}"` : ""}, with owner, origin and version`} pad={false}>
          {items.length ? (
            <div className="max-h-[620px] overflow-auto">
              <table className="os-table">
                <thead className="sticky top-0"><tr><th>Knowledge record</th><th>Family</th><th>Domain</th><th>Origin</th><th className="text-right">Ver.</th><th>Updated</th></tr></thead>
                <tbody>
                  {items.map((i) => {
                    const f = familyOf(i.knowledge_type);
                    const active = i.knowledge_id === sel?.knowledge_id;
                    return (
                      <tr key={i.knowledge_id} className={cn(active && "bg-[var(--os-hover)]")}>
                        <td className="max-w-[320px]">
                          <Link href={href({ id: i.knowledge_id })} scroll={false} className="block truncate font-bold hover:underline" title={i.title}>{i.title}</Link>
                          <div className="os-faint truncate text-[11px]">{pretty(i.knowledge_type)}{i.layer ? ` · ${LAYER[i.layer] ?? i.layer}` : ""}{i.verified_by ? " · verified" : ""}{!i.editable ? " · governed pack" : ""}</div>
                          {i.content && <div className="os-muted truncate text-[11px]" title={i.content.slice(0, 300)}>{i.content.slice(0, 140)}</div>}
                        </td>
                        <td>{f ? <Tag tone={f.tone}>{f.label.split(" ")[0]}</Tag> : <Tag>{pretty(i.knowledge_type)}</Tag>}</td>
                        <td className="os-muted">{i.domain_name}</td>
                        <td className="os-muted whitespace-nowrap">{ORIGIN[i.origin ?? ""] ?? pretty(i.origin) ?? "–"}</td>
                        <td className="text-right tabular-nums">v{i.version}</td>
                        <td className="os-faint whitespace-nowrap text-[11px]">{ago(i.updated_at ?? i.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <div className="p-5"><Empty icon={Search} title="No knowledge matches">Try another search or family.</Empty></div>}
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Knowledge record" subtitle="Content, provenance and where it was reused"
                action={sel ? <Tag tone={sel.status === "ACTIVE" ? "green" : "neutral"}>{pretty(sel.status)}</Tag> : undefined}>
            {sel ? (
              <>
                <div className="text-[14px] font-extrabold">{sel.title}</div>
                <div className="os-faint mb-3 text-[11px]">{sel.domain_name} · {pretty(sel.knowledge_type)} · v{sel.version}</div>
                <div className="os-code mb-3 max-h-[320px] overflow-auto whitespace-pre-wrap text-[11px]">{sel.content || "(no text content)"}</div>
                {sel.content_json && Object.keys(sel.content_json).length > 0 && (
                  <details className="mb-3" open>
                    <summary className="os-muted cursor-pointer text-[10.5px] font-bold uppercase tracking-wide">Structured content</summary>
                    <pre className="os-code mt-1.5 max-h-[260px] overflow-auto text-[11px]">{JSON.stringify(sel.content_json, null, 2)}</pre>
                  </details>
                )}
                <div className="grid gap-2.5 sm:grid-cols-2">
                  <Field label="Origin">{ORIGIN[sel.origin ?? ""] ?? pretty(sel.origin) ?? "–"}{sel.source_run_id ? <> · <Link className="underline" href={`/os/build?run=${sel.source_run_id}`}>{sel.run_name ?? "source run"}</Link></> : null}</Field>
                  <Field label="Owner">{sel.created_by} · {ago(sel.updated_at ?? sel.created_at)}</Field>
                  <Field label="Trust">{sel.verified_by ? `Verified by ${sel.verified_by}${sel.verified_at ? ` ${ago(sel.verified_at)}` : ""}` : sel.editable ? "Not yet verified" : "Repository pack (governed)"}</Field>
                  <Field label="Confidence">{sel.confidence != null ? `${Math.round(Number(sel.confidence) * 100)}%` : "–"}</Field>
                  <Field label="Pipeline layer">{LAYER[sel.layer ?? "ANY"] ?? sel.layer}</Field>
                  <Field label="Next review">{sel.review_due ? sel.review_due.slice(0, 10) : "–"}</Field>
                  <Field label="Reviewed by">{sel.reviewed_by ? `${sel.reviewed_by}${sel.review_note ? `: ${sel.review_note}` : ""}` : "–"}</Field>
                  <Field label="Reference">{sel.source_reference ? <span className="break-all">{sel.source_reference}</span> : "–"}</Field>
                  {!sel.editable && sel.read_only_reason && <Field label="Read-only because">{sel.read_only_reason}</Field>}
                  {!!sel.tags?.length && <Field label="Tags">{sel.tags.join(", ")}</Field>}
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                  <OpenRecord id={sel.knowledge_id} label="Full record: diff, rollback, edit" />
                </div>
                {curate !== "none" && sel.status === "ACTIVE" && (
                  <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-[var(--os-line)] p-3">
                    <span className="os-muted text-[11.5px]">{sel.verified_at ? `Verified ${ago(sel.verified_at)}${sel.review_due ? `; next review ${sel.review_due.slice(0, 10)}` : ""}.` : "A steward has not verified this record yet."}</span>
                    <VerifyKnowledge id={sel.knowledge_id} mode={curate} previewing={!!access.preview} />
                  </div>
                )}
                <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Version history ({record?.versions.length ?? 1})</div>
                <div className="os-divide mb-2 max-h-[220px] overflow-auto">
                  {(record?.versions ?? []).map((v) => (
                    <div key={v.knowledge_id} className="py-1.5 text-[11.5px]">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold">v{v.version}{v.is_current ? " · in use" : ""}</span>
                        <span className="flex gap-1"><Tag tone={v.status === "ACTIVE" ? "green" : v.status === "PROPOSED" ? "amber" : "neutral"}>{pretty(v.status)}</Tag><Tag>{ORIGIN[v.origin ?? ""] ?? pretty(v.origin) ?? "–"}</Tag></span>
                      </div>
                      <div className="os-faint">{v.created_by} · {ago(v.created_at)}{v.change_note ? ` · ${v.change_note}` : ""}{v.review_note ? ` · review: ${v.review_note}` : ""}</div>
                    </div>
                  ))}
                </div>
                <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Usage by stage ({record?.total ?? 0} uses)</div>
                {record && Object.keys(record.byStage).length ? (
                  <div className="mb-2 flex flex-wrap gap-1">{Object.entries(record.byStage).sort((a, b) => b[1] - a[1]).map(([s, n]) => <Tag key={s}>{pretty(s)} · {n}</Tag>)}</div>
                ) : <div className="os-faint mb-2 text-[12px]">No stage has retrieved it yet.</div>}
                {!!record?.outcomes.length && (
                  <>
                    <div className="os-muted mb-1.5 mt-3 text-[10.5px] font-bold uppercase tracking-wide">Track record</div>
                    <table className="os-table mb-2 text-[11.5px]">
                      <thead><tr><th>Stage</th><th className="text-right">Helpful</th><th className="text-right">Kept</th><th className="text-right">Corrected</th><th className="text-right">Rejected</th></tr></thead>
                      <tbody>{record.outcomes.map((x) => (
                        <tr key={x.stage}><td>{pretty(x.stage)}</td>
                          <td className="text-right tabular-nums">{x.helpfulness != null ? `${Math.round(x.helpfulness * 100)}%` : "–"}</td>
                          <td className="text-right tabular-nums">{x.accepted}/{x.observed}</td>
                          <td className="text-right tabular-nums">{x.corrected}</td>
                          <td className="text-right tabular-nums">{x.rejected}</td></tr>
                      ))}</tbody>
                    </table>
                  </>
                )}
                <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Reused by runs ({record?.usage.length ?? 0})</div>
                {record?.usage.length ? (
                  <div className="os-divide max-h-[260px] overflow-auto">
                    {record.usage.map((u) => (
                      <Link key={`${u.run_id}-${u.stage}-${u.version}`} href={`/os/build?run=${u.run_id}`} className="flex items-center justify-between gap-2 py-2 hover:underline">
                        <span className="min-w-0">
                          <span className="block truncate text-[12px] font-semibold">{u.run_name}</span>
                          <span className="os-faint text-[10.5px]">v{u.version} · {u.uses} use{u.uses === 1 ? "" : "s"} · last {ago(u.last_used)}</span>
                        </span>
                        <span className="flex shrink-0 gap-1"><Tag>{pretty(u.stage)}</Tag><Tag tone="green">{stateLabel(u.current_state)}</Tag></span>
                      </Link>
                    ))}
                  </div>
                ) : <div className="os-faint text-[12px]">Not used by a run yet.</div>}
              </>
            ) : <Empty icon={BookmarkCheck} title="Pick a record" />}
          </Card>

          {curate !== "none" && (
            <Card title="Steward inbox" subtitle="Agent proposals waiting to become trusted memory" pad={false}
                  action={<Tag tone={inbox.data.length ? "amber" : "green"}>{inbox.data.length} waiting</Tag>}>
              <SourceError what="The inbox" error={inbox.error} />
              {inbox.data.length ? (
                <div className="os-divide max-h-[360px] overflow-auto">
                  {inbox.data.slice(0, 12).map((i) => (
                    <div key={i.knowledge_id} className="flex items-center justify-between gap-3 px-5 py-2.5">
                      <span className="min-w-0">
                        <Link href={href({ id: i.knowledge_id })} scroll={false} className="block truncate text-[12.5px] font-bold hover:underline">{i.title}</Link>
                        <span className="os-faint text-[10.5px]">{i.domain_name ?? "No domain"} · {pretty(i.knowledge_type)} · from {ORIGIN[i.origin ?? ""] ?? (pretty(i.origin) || "an agent")} · {ago(i.created_at)}</span>
                      </span>
                      <InboxDecision id={i.knowledge_id} mode={curate} previewing={!!access.preview} />
                    </div>
                  ))}
                </div>
              ) : <div className="p-5"><Empty icon={Inbox} title="Nothing waits for curation" /></div>}
            </Card>
          )}

          <Card title="Contribution lifecycle" subtitle="How memory grows: captured automatically, trusted only after review">
            {o ? (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    ["Evidence captured", Object.values(o.by.origin).reduce((a, b) => a + b, 0), "green"],
                    ["Steward inbox", o.totals.inbox, "amber"],
                    ["Verified", o.totals.verified, "blue"],
                    ["Reused 30d", o.totals.used_30d, "purple"],
                  ].map(([l, v, t], i) => (
                    <div key={String(l)} className="relative rounded-lg border border-[var(--os-line)] p-2.5 text-center">
                      <div className="text-[18px] font-extrabold tabular-nums">{v}</div>
                      <div className="os-faint text-[10.5px]">{l}</div>
                      <span className="absolute inset-x-3 bottom-0 h-0.5 rounded" style={{ background: `var(--os-${t}-ink)` }} />
                      {i < 3 && <ArrowRight className="os-faint absolute -right-2.5 top-1/2 z-10 hidden h-3.5 w-3.5 -translate-y-1/2 sm:block" />}
                    </div>
                  ))}
                </div>
                <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Where it comes from</div>
                <div className="flex flex-col gap-1.5">
                  {Object.entries(o.by.origin).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
                    const max = Math.max(...Object.values(o.by.origin));
                    return (
                      <div key={k} className="flex items-center gap-2 text-[11.5px]">
                        <span className="w-36 shrink-0 truncate">{ORIGIN[k] ?? pretty(k)}</span>
                        <span className="h-2 flex-1 overflow-hidden rounded bg-[var(--os-line-soft)]"><span className="block h-full rounded bg-[var(--os-leaf)]" style={{ width: `${(n / max) * 100}%` }} /></span>
                        <span className="w-8 text-right tabular-nums">{n}</span>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : <Empty title="Overview unavailable" />}
          </Card>
        </div>
      </div>
    </Frame>
  );
}

/** Header, the six memory KPIs and the view tabs, shared by every view. */
function Frame({ access, o, view, domains, error, children }: {
  access: Awaited<ReturnType<typeof guard>>; o: Awaited<ReturnType<typeof loadKnowledgeOverview>>["data"]; view: View;
  domains: { domain_id: string; domain_name: string }[]; error?: string | null; children: React.ReactNode;
}) {
  const t = o?.totals;
  const tab = (v: View) => `/os/knowledge${v === "catalog" ? "" : `?view=${v}`}`;
  return (
    <>
      <PageHeader eyebrow={`${access.persona.label} / Knowledge service`} title="Knowledge hub"
                  subtitle="Enterprise Memory: governed knowledge for sources, domains and products, learned from every run, review and incident and reused by the agents."
                  actions={<><KnowledgeTools domains={domains} />{canSee(access, "skills") && <Btn href="/os/skills" variant="primary">Open skills</Btn>}</>} />
      <SourceError what="Enterprise Memory" error={error ?? null} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Active records" value={t?.active ?? "–"} hint={`${Object.keys(o?.by.domain ?? {}).length} domains`} icon={BookOpen} tone="green" />
        <Kpi label="Learned in 7 days" value={t?.learned_7d ?? "–"} hint="from approvals and generation" icon={Sparkles} tone="purple" href={tab("feed")} />
        <Kpi label="Awaiting curation" value={t?.inbox ?? "–"} hint="agent proposals for a steward" icon={Inbox} tone="amber" href={tab("inbox")} />
        <Kpi label="Verified" value={t?.verified ?? "–"} hint="checked by a person" icon={BadgeCheck} tone="green" />
        <Kpi label="Stale" value={t?.stale ?? "–"} hint="due for review" icon={Clock} tone="red" href={tab("health")} />
        <Kpi label="Used by runs (30d)" value={t?.used_30d ?? "–"} hint={`${t?.uses_30d ?? 0} retrievals`} icon={TrendingUp} tone="blue" href={tab("ask")} />
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-x-5 border-b border-[var(--os-line)]" role="tablist" aria-label="Knowledge views">
        {VIEWS.map((v) => (
          <Link key={v.key} href={tab(v.key)} role="tab" aria-selected={view === v.key} className="os-tab">
            {v.label}{v.key === "inbox" && !!t?.inbox && <span className="os-faint"> {t.inbox}</span>}
          </Link>
        ))}
      </div>
      {children}
    </>
  );
}

/** The full Enterprise Memory tools: Ask AI with citations, every item with filters and editing, inbox with diffs and sources, feed, health. */
async function ToolView({ view, sp, access }: { view: Exclude<View, "catalog">; sp: Params; access: Awaited<ReturnType<typeof guard>> }) {
  const domainId = sp.domain_id ?? sp.domain;
  const scope = domainId ? `?domain_id=${encodeURIComponent(domainId)}` : "";
  const [overview, domains] = await Promise.all([loadKnowledgeOverview(), loadDomains()]);
  const list = domains.data.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }));
  let body: React.ReactNode;
  if (view === "ask") {
    const full = await api<React.ComponentProps<typeof OverviewPanel>["overview"]>(`/api/knowledge/overview${scope}`).catch(() => null);
    body = full ? (
      <OverviewPanel overview={full} ask={
        <Card title="Ask the knowledge" subtitle="Search uses Cortex Search over records in use; Ask AI answers from the matching records and cites them.">
          <KnowledgeSearch domains={domains.data.map((d) => d.domain_name)} />
        </Card>
      } />
    ) : <Empty title="Overview unavailable" />;
  } else if (view === "domains") {
    body = <DomainsGrid showDeleted={sp.deleted === "1"} />;
  } else if (view === "items") {
    const offset = Math.max(0, Number(sp.offset ?? 0) || 0);
    const q = new URLSearchParams({ offset: String(offset), limit: String(LIMIT) });
    if (sp.q) q.set("q", sp.q);
    if (domainId) q.set("domain_id", domainId);
    if (sp.type) q.set("knowledge_type", sp.type);
    if (sp.layer) q.set("layer", sp.layer);
    if (sp.status !== "ALL") q.set("status", sp.status || "ACTIVE");
    const r = await api<{ items: KnowledgeItem[]; total: number }>(`/api/knowledge?${q}`).catch(() => ({ items: [], total: 0 }));
    body = <KnowledgeBrowser items={r.items} total={r.total} offset={offset} limit={LIMIT} domains={list} />;
  } else if (view === "inbox") {
    const r = await api<{ items: InboxItem[] }>(`/api/knowledge/inbox${scope}`).catch(() => ({ items: [] }));
    body = <InboxPanel items={r.items} />;
  } else if (view === "feed") {
    const q = new URLSearchParams({ limit: "150" });
    if (domainId) q.set("domain_id", domainId);
    if (sp.origin) q.set("origin", sp.origin);
    const r = await api<{ items: FeedItem[] }>(`/api/knowledge/feed?${q}`).catch(() => ({ items: [] }));
    body = <FeedPanel items={r.items} />;
  } else {
    const [stale, dupes] = await Promise.all([
      api<{ items: KnowledgeItem[]; stale_days: number }>(`/api/knowledge/stale${scope}`).catch(() => ({ items: [], stale_days: 180 })),
      api<{ pairs: Pair[] }>(`/api/knowledge/duplicates${scope}`).catch(() => ({ pairs: [] })),
    ]);
    body = <HealthPanel stale={stale.items} staleDays={stale.stale_days} pairs={dupes.pairs} />;
  }
  return <Frame access={access} o={overview.data} view={view} domains={list} error={overview.error}>{body}</Frame>;
}
