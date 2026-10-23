import Link from "next/link";
import { ArrowRight, BookOpen, BookmarkCheck, Inbox, Search, Sparkles, TrendingUp } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadDomains, loadKnowledge, loadKnowledgeOverview, loadKnowledgeRecord } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { canSee } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Knowledge hub" };

/** Enterprise Memory's knowledge types, in the hub's five families. */
const FAMILIES: { key: string; label: string; types: string[]; tone: Tone }[] = [
  { key: "domain", label: "Domain and business", types: ["GLOSSARY", "MODEL_DEFINITION", "BUSINESS_RULE", "NAMING_STANDARD", "ONBOARDING_GUIDE"], tone: "green" },
  { key: "mapping", label: "Mapping and transformation", types: ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "STTM_TEMPLATE"], tone: "blue" },
  { key: "quality", label: "Quality and testing", types: ["SODA_PATTERN", "QA_TEST", "EXCEPTION"], tone: "amber" },
  { key: "engineering", label: "Engineering patterns", types: ["DBT_PATTERN", "AIRFLOW_DAG"], tone: "purple" },
  { key: "operational", label: "Operational learning", types: ["AIRFLOW_RUNBOOK"], tone: "red" },
];
const familyOf = (type: string) => FAMILIES.find((f) => f.types.includes(type));

const ORIGIN: Record<string, string> = {
  SEED: "Domain pack", MAPPING: "Mapping reviews", SODA: "Data quality approvals", DBT: "Code generation", REVIEW: "Code review", OPS: "Operations", USER: "People",
};

export default async function KnowledgeHub({ searchParams }: { searchParams: { q?: string; family?: string; domain?: string; id?: string } }) {
  const access = await guard("knowledge");
  const persona = access.persona;
  const family = FAMILIES.find((f) => f.key === searchParams.family);
  const params: Record<string, string> = {};
  if (searchParams.q) params.q = searchParams.q;
  if (searchParams.domain) params.domain_id = searchParams.domain;
  const [overview, domains, list] = await Promise.all([loadKnowledgeOverview(), loadDomains(), loadKnowledge(params)]);
  const items = list.data.items.filter((i) => !family || family.types.includes(i.knowledge_type));
  const selectedId = searchParams.id ?? items[0]?.knowledge_id;
  const record = selectedId ? await loadKnowledgeRecord(selectedId) : null;
  const sel = record?.item ?? items.find((i) => i.knowledge_id === selectedId) ?? null;
  const o = overview.data;
  const href = (patch: Record<string, string | undefined>) => {
    const p = new URLSearchParams();
    const merged = { q: searchParams.q, family: searchParams.family, domain: searchParams.domain, ...patch };
    Object.entries(merged).forEach(([k, v]) => v && p.set(k, v));
    const qs = p.toString();
    return `/os/knowledge${qs ? `?${qs}` : ""}`;
  };
  const familyCount = (f: (typeof FAMILIES)[number]) => f.types.reduce((n, t) => n + (o?.by.type[t] ?? 0), 0);

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Knowledge service`} title="Knowledge hub"
                  subtitle="Enterprise Memory: governed knowledge for sources, domains and products, learned from every run, review and incident and reused by the agents."
                  actions={<><Action access={access} id="curateKnowledge" href="/knowledge" />{canSee(access, "skills") && <Btn href="/os/skills" variant="primary">Open skills</Btn>}</>} />
      <SourceError what="Enterprise Memory" error={overview.error ?? list.error} />

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Active records" value={o?.totals.active ?? "–"} hint={`${Object.keys(o?.by.domain ?? {}).length} domains`} icon={BookOpen} tone="green" />
        <Kpi label="Learned in 7 days" value={o?.totals.learned_7d ?? "–"} hint="from approvals and generation" icon={Sparkles} tone="purple" />
        <Kpi label="Awaiting curation" value={o?.totals.inbox ?? "–"} hint="agent proposals for a steward" icon={Inbox} tone="amber" href="/knowledge?tab=inbox" />
        <Kpi label="Used by runs (30d)" value={o?.totals.used_30d ?? "–"} hint={`${o?.totals.uses_30d ?? 0} retrievals`} icon={TrendingUp} tone="blue" />
      </div>

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
                <thead className="sticky top-0"><tr><th>Knowledge record</th><th>Family</th><th>Domain</th><th>Origin</th><th className="text-right">Ver.</th></tr></thead>
                <tbody>
                  {items.slice(0, 80).map((i) => {
                    const f = familyOf(i.knowledge_type);
                    const active = i.knowledge_id === sel?.knowledge_id;
                    return (
                      <tr key={i.knowledge_id} className={cn(active && "bg-[var(--os-hover)]")}>
                        <td className="max-w-[320px]">
                          <Link href={href({ id: i.knowledge_id })} scroll={false} className="block truncate font-bold hover:underline" title={i.title}>{i.title}</Link>
                          <div className="os-faint truncate text-[11px]">{pretty(i.knowledge_type)}{i.verified_by ? " · verified" : ""}</div>
                        </td>
                        <td>{f ? <Tag tone={f.tone}>{f.label.split(" ")[0]}</Tag> : <Tag>{pretty(i.knowledge_type)}</Tag>}</td>
                        <td className="os-muted">{i.domain_name}</td>
                        <td className="os-muted whitespace-nowrap">{ORIGIN[i.origin ?? ""] ?? pretty(i.origin) ?? "–"}</td>
                        <td className="text-right tabular-nums">v{i.version}</td>
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
                <div className="os-code mb-3 max-h-[180px] whitespace-pre-wrap text-[11px]">{sel.content?.slice(0, 900) || "(no text content)"}</div>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  <Field label="Origin">{ORIGIN[sel.origin ?? ""] ?? pretty(sel.origin) ?? "–"}{sel.source_run_id ? <> · <Link className="underline" href={`/os/build?run=${sel.source_run_id}`}>source run</Link></> : null}</Field>
                  <Field label="Owner">{sel.created_by} · {ago(sel.updated_at ?? sel.created_at)}</Field>
                  <Field label="Trust">{sel.verified_by ? `Verified by ${sel.verified_by}` : sel.editable ? "Not yet verified" : "Repository pack (governed)"}</Field>
                  <Field label="Versions">{record?.versions.length ?? 1} version{(record?.versions.length ?? 1) === 1 ? "" : "s"}{sel.change_note ? ` · ${sel.change_note}` : ""}</Field>
                </div>
                <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Reused by runs</div>
                {record?.usage.length ? (
                  <div className="os-divide">
                    {record.usage.slice(0, 5).map((u) => (
                      <Link key={`${u.run_id}-${u.stage}`} href={`/os/build?run=${u.run_id}`} className="flex items-center justify-between gap-2 py-2 hover:underline">
                        <span className="min-w-0 truncate text-[12px] font-semibold">{u.run_name}</span>
                        <span className="flex shrink-0 gap-1"><Tag>{pretty(u.stage)}</Tag><Tag tone="green">{stateLabel(u.current_state)}</Tag></span>
                      </Link>
                    ))}
                  </div>
                ) : <div className="os-faint text-[12px]">Not used by a run yet.</div>}
              </>
            ) : <Empty icon={BookmarkCheck} title="Pick a record" />}
          </Card>

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
    </>
  );
}
