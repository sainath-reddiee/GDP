import { CheckCircle2, Inbox, SearchX, ShieldCheck } from "lucide-react";
import { loadDomains, loadRequests } from "@/lib/os/data";
import { SECTIONS, has, screenByKey, type ScreenKey } from "@/lib/os/access";
import { osAccess, type OsAccess } from "@/lib/os/session";
import { intentOf, overdue, type OnboardingRequest, type RequestType } from "@/lib/os/requests";
import { Banner, Btn, Empty, SourceError } from "@/components/os/ui";
import { OnboardedDomains, ProductsPanel, StartPanel, WorkflowRow } from "@/components/os/home";
import { workflowHref } from "@/lib/os/work";
import { Segmented } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";

export const dynamic = "force-dynamic";
export const metadata = { title: "Home" };

// Home, where everyone lands whatever their role: every build workflow (an onboarding request), the actions first,
// with the step it is at and who it waits on, filtered in the URL (?view=active|action|all&type=
// &intent=&q=), the panel that starts a new one or enhances what exists, the data products, and the onboarded domains below. A workflow
// opens on the stage board it is at, selected (lib/os/work workflowHref).

const screenByKeySafe = (key: string) => {
  try { return screenByKey(key as ScreenKey) ?? null; } catch { return null; }
};

const VIEWS = [
  { key: "active", label: "Active" },
  { key: "action", label: "Action required" },
  { key: "all", label: "All" },
] as const;
const TYPES: { key: string; label: string; type?: RequestType }[] = [
  { key: "all", label: "All" }, { key: "domain", label: "Domain", type: "DOMAIN" }, { key: "source", label: "Source", type: "SOURCE" },
  { key: "product", label: "Product", type: "PRODUCT" },
];
const INTENTS = [{ key: "all", label: "Any" }, { key: "new", label: "New" }, { key: "enhance", label: "Enhance" }] as const;
const PRIORITY = { HIGH: 0, MEDIUM: 1, LOW: 2 } as const;

type Params = { view?: string; type?: string; intent?: string; q?: string; denied?: string };

/** The workflow's next step is the viewer's (or the previewed role's). */
const yours = (r: OnboardingRequest, access: OsAccess) => r.status === "OPEN" && r.waits.some((w) => has(access, w.privilege));

/** Theirs first, then blocked or late, then by priority, then the longest waiting; finished ones last, newest first. */
function order(rows: OnboardingRequest[], access: OsAccess) {
  const waited = (r: OnboardingRequest) => r.stage_changed_at ?? r.updated_at ?? r.created_at;
  return [...rows].sort((a, b) =>
    Number(b.status === "OPEN") - Number(a.status === "OPEN")
    || Number(yours(b, access)) - Number(yours(a, access))
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

  const [list, domains] = await Promise.all([loadRequests("all", "ALL"), loadDomains()]);
  const all = list.data.requests;
  const active = all.filter((r) => r.status === "OPEN");
  const action = active.filter((r) => yours(r, access));
  const views = { active, action, all };

  const view = VIEWS.find((v) => v.key === searchParams.view)?.key ?? (action.length ? "action" : "active");
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const intent = INTENTS.find((i) => i.key === searchParams.intent) ?? INTENTS[0];
  const q = (searchParams.q ?? "").trim().toLowerCase();
  const rows = order(views[view].filter((r) =>
    (!type.type || r.request_type === type.type)
    && (intent.key === "all" || intentOf(r) === intent.key.toUpperCase())
    && (!q || r.title.toLowerCase().includes(q))), access);

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

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0" aria-labelledby="workflows-title">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h1 id="workflows-title" className="text-[20px] font-extrabold tracking-[-0.03em]">Build workflows</h1>
            <HomeSearch />
          </div>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Segmented label="Workflows to show" testId="home-views" options={VIEWS.map((v) => ({
              key: v.key, label: v.label, href: href({ view: v.key }), active: v.key === view, count: views[v.key].length,
              dot: v.key === "action" && action.length > 0,
            }))} />
            <span className="mx-1 hidden h-5 w-px bg-[var(--os-line)] sm:block" aria-hidden />
            <Segmented label="Type" testId="home-types" options={TYPES.map((t) => ({ key: t.key, label: t.label, href: href({ type: t.key }), active: t.key === type.key }))} />
            <Segmented label="New or enhance" testId="home-intents" options={INTENTS.map((i) => ({ key: i.key, label: i.label, href: href({ intent: i.key }), active: i.key === intent.key }))} />
          </div>

          {rows.length ? (
            <div className="flex flex-col gap-3">
              {rows.map((r) => <WorkflowRow key={r.request_id} r={r} access={access} href={workflowHref(r)} />)}
            </div>
          ) : filtered ? (
            <Empty icon={SearchX} title={q ? `No workflow matches "${searchParams.q?.trim()}"` : "No workflow matches these filters"}
                   action={<Btn href={href({ type: "all", intent: "all", q: "" })}>Clear filters</Btn>} />
          ) : view === "action" ? (
            <Empty icon={CheckCircle2} title="Nothing waits on you">When a workflow reaches a step your role owns, it shows here with what to do.</Empty>
          ) : (
            <Empty icon={Inbox} title={view === "active" ? "No active workflows" : "No workflows yet"}>
              Start one from <b>Start a workflow</b>: a new domain, source or data product, or more from one already on the platform.
            </Empty>
          )}
        </section>

        <aside>
          <div className="flex flex-col gap-4">
            <StartPanel access={access} />
            <div className="hidden xl:block"><ProductsPanel domains={domains.data} /></div>
          </div>
        </aside>
      </div>
      {/* below xl the data products follow the workflows */}
      <div className="mt-5 xl:hidden"><ProductsPanel domains={domains.data} /></div>
      {/* the domains come after the work: actions first */}
      <div className="mt-8">
        <SourceError what="Domains" error={domains.error} />
        <OnboardedDomains domains={domains.data} requests={all} access={access} />
      </div>
    </div>
  );
}
