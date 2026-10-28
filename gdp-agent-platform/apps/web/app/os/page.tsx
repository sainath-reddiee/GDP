import { CheckCircle2, Inbox, SearchX, ShieldCheck } from "lucide-react";
import { loadDomains, loadRequests, loadWorkflowSearch } from "@/lib/os/data";
import { SECTIONS, screenByKey, type ScreenKey } from "@/lib/os/access";
import { osAccess, type OsAccess } from "@/lib/os/session";
import { intentOf, overdue, type OnboardingRequest } from "@/lib/os/requests";
import { Banner, Btn, Empty, SourceError } from "@/components/os/ui";
import { OnboardedDomains, StartPanel, WorkflowRow } from "@/components/os/home";
import { inflight, requestNeedsMe, workflowHref } from "@/lib/os/work";
import type { ActionType } from "@/lib/os/actions";
import { Segmented } from "@/components/os/stage-board";
import { PlatformSearch } from "@/components/os/platform-search";

export const dynamic = "force-dynamic";
export const metadata = { title: "Home" };

// Home, where everyone lands whatever their role: every build workflow (an onboarding request),
// with the step it is at and who it waits on, filtered in the URL (?view=active|action|all&type=
// &intent=&q=), the panel that starts a new one or enhances what exists (a data product among them), and the onboarded domains below. A workflow
// opens on Initiate, selected (lib/os/work workflowHref); from there "Open where it is" goes to its stage.

const screenByKeySafe = (key: string) => {
  try { return screenByKey(key as ScreenKey) ?? null; } catch { return null; }
};

const VIEWS = [
  { key: "active", label: "Active" },
  { key: "action", label: "Action required" },
  { key: "all", label: "All" },
] as const;
const TYPES: { key: string; label: string; type?: ActionType }[] = [
  { key: "all", label: "All" }, { key: "domain", label: "Domain", type: "DOMAIN" }, { key: "source", label: "Source", type: "SOURCE" },
  { key: "product", label: "Product", type: "PRODUCT" },
];
const INTENTS = [{ key: "all", label: "Any" }, { key: "new", label: "New" }, { key: "enhance", label: "Enhance" }] as const;
const PRIORITY = { HIGH: 0, MEDIUM: 1, LOW: 2 } as const;

type Params = { view?: string; type?: string; intent?: string; q?: string; denied?: string };

/** The workflow's next step is the viewer's (or the previewed role's). */
const yours = (r: OnboardingRequest, access: OsAccess) => requestNeedsMe(r, access);

/** Blocked or late first, then by priority, then the longest waiting; finished ones last, newest first. Never by whose
 *  turn it is: Home's order is the same under every persona. */
function order(rows: OnboardingRequest[], _access?: OsAccess) {
  const waited = (r: OnboardingRequest) => r.stage_changed_at ?? r.updated_at ?? r.created_at;
  return [...rows].sort((a, b) =>
    Number(b.status === "OPEN") - Number(a.status === "OPEN")
    || Number(!!b.attention || overdue(b)) - Number(!!a.attention || overdue(a))
    || (PRIORITY[a.priority] ?? 1) - (PRIORITY[b.priority] ?? 1)
    || (a.status === "OPEN" ? waited(a).localeCompare(waited(b)) : (b.closed_at ?? b.updated_at).localeCompare(a.closed_at ?? a.updated_at)));
}

export default async function Home({ searchParams }: { searchParams: Params }) {
  const access = await osAccess();
  const denied = searchParams.denied ? screenByKeySafe(searchParams.denied) : null;
  // the rail entry and its tab ("Build: Workbench"), or the entry alone when they share a name
  const deniedSection = denied ? SECTIONS.find((x) => x.key === denied.section)?.label : null;
  const deniedName = denied ? (deniedSection && deniedSection !== denied.label ? `${deniedSection}: ${denied.label}` : denied.label) : "";

  const query = (searchParams.q ?? "").trim();
  // Home is the platform's master data: read with the person's own access, the same under every persona they work as
  const [list, domains, searched] = await Promise.all([loadRequests("all", "ALL", undefined, true), loadDomains(),
    query ? loadWorkflowSearch(query) : Promise.resolve(null)]);
  const all = list.data.requests;
  const recentDoneCutoff = Date.now() - 2 * 3600 * 1000;
  const active = all.filter(
    (r) =>
      r.status === "OPEN" ||
      (r.status === "DONE" && new Date(r.closed_at ?? r.updated_at).getTime() >= recentDoneCutoff),
  );
  const action = active.filter((r) => yours(r, access));
  const views = { active, action, all };

  // the same opening view under every persona: everything open
  const view = VIEWS.find((v) => v.key === searchParams.view)?.key ?? "active";
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const intent = INTENTS.find((i) => i.key === searchParams.intent) ?? INTENTS[0];
  // the search bar matches workflows by meaning (synonyms, intent such as "waiting on me"), best first; when the search
  // is not reachable, by their title.
  const q = query.toLowerCase();
  const only = searched?.data && !searched.data.intent.terms.length && (searched.data.intent.mine || searched.data.intent.failed)
    ? searched.data.intent : null;
  const rank = searched?.data && !only ? new Map(searched.data.results.map((x, i) => [x.id, i])) : null;
  const matches = (r: OnboardingRequest) => !q
    || (only ? (!only.mine || yours(r, access)) && (!only.failed || !!r.attention)
      : rank ? rank.has(r.request_id) : r.title.toLowerCase().includes(q) || (r.domain_name ?? "").toLowerCase().includes(q));
  const intentOk = (r: OnboardingRequest) => intent.key === "all" || intentOf(r) === intent.key.toUpperCase();

  type Entry = { kind: "workflow"; r: OnboardingRequest };
  const typeOf = (e: Entry): ActionType => e.r.request_type;
  const poolFor = (v: keyof typeof views): Entry[] =>
    order(views[v].filter((r) => intentOk(r) && matches(r)), access).map((r) => ({ kind: "workflow" as const, r }));
  const pool = poolFor(view);
  const ofType = (k: (typeof TYPES)[number]) => (k.type ? pool.filter((e) => typeOf(e) === k.type) : pool);
  const urgency = (e: Entry) => e.r.attention ? 0 : e.r.status === "OPEN" ? 1 : 5;
  const entries = ofType(type).map((e, i) => ({ e, i }));
  const queue = (rank
    ? entries.sort((x, y) => (rank.get(x.e.r.request_id) ?? 0) - (rank.get(y.e.r.request_id) ?? 0))
    : entries.sort((x, y) => urgency(x.e) - urgency(y.e) || x.i - y.i)).map((x) => x.e);
  const counts = { action: action.length, active: active.length, all: all.length };
  // matches the current view leaves out (a finished workflow, one not waiting on you), offered as "show all"
  const elsewhereMatches = q ? (type.type ? poolFor("all").filter((e) => typeOf(e) === type.type) : poolFor("all")).length : 0;

  const href = (patch: Partial<Params>) => {
    const next = { view, type: type.key, intent: intent.key, q: searchParams.q ?? "", ...patch };
    // the view always (its default depends on the queue); a filter only when it narrows
    const p = new URLSearchParams(Object.entries(next).filter(([k, v]) => v && (k === "view" || v !== "all")) as [string, string][]);
    return `/os?${p}`;
  };
  const filtered = type.key !== "all" || intent.key !== "all" || !!q;

  return (
    <div data-testid="home">
      {denied && (
        <Banner tone="amber" icon={ShieldCheck}>
          <b>{deniedName} is not part of your role.</b> It is open to {denied.why}{denied.view ? ` (any of ${denied.view.join(", ")})` : ""}. Ask a governance admin if you need it.
        </Banner>
      )}
      <SourceError what="Workflows" error={list.error} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_330px] xl:grid-cols-[minmax(0,1fr)_364px] 2xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="min-w-0" aria-labelledby="workflows-title">
          {/* the toolbar stays in reach while the workflows scroll under it */}
          <div className="sticky top-[58px] z-30 -mx-1 mb-4 bg-[var(--os-bg)]/95 px-1 pb-3 pt-1 backdrop-blur supports-[backdrop-filter]:bg-[var(--os-bg)]/80" data-testid="home-toolbar">
            <div className="mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
              <h1 id="workflows-title" className="shrink-0 text-[20px] font-extrabold tracking-[-0.03em]">Build Workflows</h1>
              <div className="min-w-[260px] flex-1"><PlatformSearch /></div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented label="Workflows to show" testId="home-views" options={VIEWS.map((v) => ({
                key: v.key, label: v.label, href: href({ view: v.key }), active: v.key === view, count: counts[v.key],
                dot: v.key === "action" && counts.action > 0,
              }))} />
              <span className="mx-1 hidden h-5 w-px bg-[var(--os-line)] sm:block" aria-hidden />
              <Segmented label="Type" testId="home-types" options={TYPES.map((t) => ({ key: t.key, label: t.label, href: href({ type: t.key }), active: t.key === type.key,
                count: ofType(t).length }))} />
              <Segmented label="New or enhance" testId="home-intents" options={INTENTS.map((i) => ({ key: i.key, label: i.label, href: href({ intent: i.key }), active: i.key === intent.key,
                ...(i.key === "all" ? {} : { count: views[view].filter((r) => intentOf(r) === i.key.toUpperCase() && matches(r) && (!type.type || r.request_type === type.type)).length }) }))} />
              {q && (
                <span className="os-faint text-[11px]" data-testid="search-mode">
                  {rank ? `${queue.length} matching "${query}" by meaning` : `${queue.length} with "${query}" in the name`}
                  {elsewhereMatches > queue.length && view !== "all" && (
                    <> · <a className="os-accent font-bold" href={href({ view: "all" })}>show all {elsewhereMatches}</a></>
                  )}
                </span>
              )}
            </div>
          </div>

          {queue.length ? (
            <div className="os-scroll-y flex max-h-[500px] flex-col gap-3 pr-2" data-testid="platform-queue">
              {queue.map((e) => <WorkflowRow key={e.r.request_id} r={e.r} access={access} href={workflowHref(e.r)} />)}
            </div>
          ) : filtered ? (
            <Empty icon={SearchX} title={q ? `Nothing matches "${searchParams.q?.trim()}"` : "Nothing matches these filters"}
                   action={<Btn href={href({ type: "all", intent: "all", q: "" })}>Clear filters</Btn>} />
          ) : view === "action" ? (
            <Empty icon={CheckCircle2} title="Nothing waits on you">When a workflow reaches your role, it shows here with what to do.</Empty>
          ) : (
            <Empty icon={Inbox} title={view === "active" ? "No active workflows" : "No workflows yet"}>
              Start one from <b>Start a workflow</b>: a new domain, source or data product, or more from one already on the platform.
            </Empty>
          )}
        </section>

        <aside className="min-w-0">
          <div className="flex flex-col gap-4 lg:sticky lg:top-[76px]">
            <StartPanel access={access} inflight={inflight(all)} />
          </div>
        </aside>
      </div>
      {/* the domains come after the work: actions first */}
      <div className="mt-6">
        <SourceError what="Domains" error={domains.error} />
        <OnboardedDomains domains={domains.data} requests={all} access={access} />
      </div>
    </div>
  );
}
