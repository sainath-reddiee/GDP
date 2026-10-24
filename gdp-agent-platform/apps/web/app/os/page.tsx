import { CheckCircle2, Inbox, SearchX, ShieldCheck } from "lucide-react";
import { loadDomains, loadRequests, loadWorkflowSearch } from "@/lib/os/data";
import { SECTIONS, screenByKey, type ScreenKey } from "@/lib/os/access";
import { osAccess, type OsAccess } from "@/lib/os/session";
import { intentOf, overdue, type OnboardingRequest } from "@/lib/os/requests";
import { Banner, Btn, Empty, SourceError } from "@/components/os/ui";
import { OnboardedDomains, PlatformActionRow, StartPanel, WorkflowRow } from "@/components/os/home";
import { inflight, requestNeedsMe, workflowHref } from "@/lib/os/work";
import { loadActions, type ActionItem, type ActionType } from "@/lib/os/actions";
import { Segmented } from "@/components/os/stage-board";
import { PlatformSearch } from "@/components/os/platform-search";

export const dynamic = "force-dynamic";
export const metadata = { title: "Home" };

// Home, where everyone lands whatever their role: every build workflow (an onboarding request), the actions first,
// with the step it is at and who it waits on, filtered in the URL (?view=active|action|all&type=
// &intent=&q=), the panel that starts a new one or enhances what exists (a data product among them), and the onboarded domains below. A workflow
// opens on Intake, selected (lib/os/work workflowHref); from there "Open where it is" goes to its stage.

const screenByKeySafe = (key: string) => {
  try { return screenByKey(key as ScreenKey) ?? null; } catch { return null; }
};

const VIEWS = [
  { key: "active", label: "Active" },
  { key: "action", label: "Action required" },
  { key: "all", label: "All" },
] as const;
// what an action is about: a domain (domain workflows, models, checks, knowledge), a source (source workflows, Silver
// runs), a data product (product workflows, Gold runs), or the platform (approvals, fixes, skills, incidents)
const TYPES: { key: string; label: string; type?: ActionType }[] = [
  { key: "all", label: "All" }, { key: "domain", label: "Domain", type: "DOMAIN" }, { key: "source", label: "Source", type: "SOURCE" },
  { key: "product", label: "Product", type: "PRODUCT" }, { key: "platform", label: "Platform", type: "PLATFORM" },
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
  const active = all.filter((r) => r.status === "OPEN");
  const action = active.filter((r) => yours(r, access));
  const views = { active, action, all };
  // every other open action on the platform, whoever acts on it (runs at a person's step that no workflow here holds,
  // approvals, models and checks in review, knowledge and skill proposals, fixes, incidents), each naming its persona.
  // Active and All show them all; Action required keeps the workflows and actions the current persona acts on.
  const covered = new Set(active.flatMap((r) => r.items.map((i) => i.run_id)).filter(Boolean) as string[]);
  const platform = await loadActions(access, covered);
  const mineItems = platform.items.filter((a) => a.mine);

  // the same opening view under every persona: everything open
  const view = VIEWS.find((v) => v.key === searchParams.view)?.key ?? "active";
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const intent = INTENTS.find((i) => i.key === searchParams.intent) ?? INTENTS[0];
  // the search bar matches workflows by meaning (synonyms, intent such as "waiting on me"), best first; when the search
  // is not reachable, by their title. Other actions match on their words (intent words alone keep them all).
  const q = query.toLowerCase();
  // a query that is only intent ("waiting on me", "failed") filters with Home's own rule over every workflow: the
  // search sees only a request's first wait and caps its results
  const only = searched?.data && !searched.data.intent.terms.length && (searched.data.intent.mine || searched.data.intent.failed)
    ? searched.data.intent : null;
  const rank = searched?.data && !only ? new Map(searched.data.results.map((x, i) => [x.id, i])) : null;
  const matches = (r: OnboardingRequest) => !q
    || (only ? (!only.mine || yours(r, access)) && (!only.failed || !!r.attention)
      : rank ? rank.has(r.request_id) : r.title.toLowerCase().includes(q));
  const terms = searched?.data ? searched.data.intent.terms : q.split(/\s+/).filter(Boolean);
  const failedOnly = !!searched?.data?.intent.failed;
  const platformMatches = (a: ActionItem) => (!failedOnly || a.tone === "red")
    && terms.every((t) => `${a.title} ${a.detail} ${a.group}`.toLowerCase().includes(t.toLowerCase().replace(/s$/, "")));
  const intentOk = (r: OnboardingRequest) => intent.key === "all" || intentOf(r) === intent.key.toUpperCase();

  // the queue for a view before the type filter: its workflows and every matching platform action (New and Enhance are
  // about workflows, so they leave the platform actions out)
  type Entry = { kind: "workflow"; r: OnboardingRequest } | { kind: "platform"; a: ActionItem };
  const typeOf = (e: Entry): ActionType => (e.kind === "workflow" ? e.r.request_type : e.a.type);
  const poolFor = (v: keyof typeof views): Entry[] => [
    ...order(views[v].filter((r) => intentOk(r) && matches(r)), access).map((r) => ({ kind: "workflow" as const, r })),
    ...(intent.key === "all" ? (v === "action" ? mineItems : platform.items).filter(platformMatches).map((a) => ({ kind: "platform" as const, a })) : []),
  ];
  const pool = poolFor(view);
  const ofType = (k: (typeof TYPES)[number]) => (k.type ? pool.filter((e) => typeOf(e) === k.type) : pool);
  // one queue: what needs attention first (failed runs and validations), then the viewer's workflows, then the other
  // actions by urgency; outside Action required the remaining workflows follow. A search keeps its best match first.
  // urgency alone (never whose turn it is), so the order is the same under every persona
  const urgency = (e: Entry) => e.kind === "platform"
    ? ({ red: 0, amber: 2, purple: 3, blue: 4 } as const)[e.a.tone]
    : e.r.attention ? 0 : e.r.status === "OPEN" ? 1 : 5;
  const entries = ofType(type).map((e, i) => ({ e, i }));
  const queue = (rank
    ? entries.sort((x, y) => (x.e.kind === "workflow" ? rank.get(x.e.r.request_id) ?? 0 : 1e6 + x.i) - (y.e.kind === "workflow" ? rank.get(y.e.r.request_id) ?? 0 : 1e6 + y.i))
    : entries.sort((x, y) => urgency(x.e) - urgency(y.e) || x.i - y.i)).map((x) => x.e);
  // the view counts are everything in each view (the filters below narrow the list, and show their own counts)
  const counts = { action: action.length + mineItems.length, active: active.length + platform.items.length, all: all.length + platform.items.length };
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
              <h1 id="workflows-title" className="shrink-0 text-[20px] font-extrabold tracking-[-0.03em]">Platform actions</h1>
              <div className="min-w-[260px] flex-1"><PlatformSearch /></div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented label="Actions to show" testId="home-views" options={VIEWS.map((v) => ({
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

          {platform.errors > 0 && (
            <p className="mb-3 text-[11px] text-[var(--os-amber-ink)]">Some platform queues could not be read; the list may be incomplete.</p>
          )}
          {queue.length ? (
            <div className="flex flex-col gap-3" data-testid="platform-queue">
              {queue.map((e) => e.kind === "workflow"
                ? <WorkflowRow key={e.r.request_id} r={e.r} access={access} href={workflowHref(e.r)} />
                : <PlatformActionRow key={e.a.key} a={e.a} />)}
            </div>
          ) : filtered ? (
            <Empty icon={SearchX} title={q ? `Nothing matches "${searchParams.q?.trim()}"` : "Nothing matches these filters"}
                   action={<Btn href={href({ type: "all", intent: "all", q: "" })}>Clear filters</Btn>} />
          ) : view === "action" ? (
            <Empty icon={CheckCircle2} title="Nothing waits on you">When a workflow, a gate, an approval or a review reaches your role, it shows here with what to do.</Empty>
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
