import { loadRequest, loadRequests, loadWorkflowSearch } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { boardView, inflight, requestNeedsMe } from "@/lib/os/work";
import { intentOf, type OnboardingRequest } from "@/lib/os/requests";
import { SourceError } from "@/components/os/ui";
import { BoardList, BoardTabs } from "@/components/os/stage-board";
import { RequestFocus, StartPanel, WorkflowRow } from "@/components/os/home";
import { HomeSearch } from "@/components/os/home-search";

export const dynamic = "force-dynamic";
export const metadata = { title: "Initiate" };

// Intake: every onboarding request by Selected, Action required, Active and Completed, and New or Enhance on the right.
// The form that files one is /os/intake/new.

export default async function IntakeBoard({ searchParams }: { searchParams: { view?: string; request?: string; q?: string; intent?: string } }) {
  const access = await guard("intake");
  const query = (searchParams.q ?? "").trim();
  const [list, picked, searched] = await Promise.all([loadRequests("all", "ALL"), searchParams.request ? loadRequest(searchParams.request) : null,
    query ? loadWorkflowSearch(query) : null]);
  const q = query.toLowerCase();
  // by meaning when the search answers ("waiting on me", synonyms), else by title
  // "waiting on me" or "failed" alone: the board's own rule over every request (the search sees only a request's first wait)
  const only = searched?.data && !searched.data.intent.terms.length && (searched.data.intent.mine || searched.data.intent.failed)
    ? searched.data.intent : null;
  const hits = searched?.data && !only ? new Set(searched.data.results.map((x) => x.id)) : null;
  // New or Enhance, from the in-flight counts on the right
  const intent = searchParams.intent === "new" || searchParams.intent === "enhance" ? searchParams.intent : null;
  const all = list.data.requests.filter((r) => (!q || (only ? (!only.mine || requestNeedsMe(r, access)) && (!only.failed || !!r.attention)
    : hits ? hits.has(r.request_id) : r.title.toLowerCase().includes(q))) && (!intent || intentOf(r) === intent.toUpperCase()));
  const yours = (r: OnboardingRequest) => requestNeedsMe(r, access);
  const lists = {
    action: all.filter(yours),
    active: all.filter((r) => r.status === "OPEN"),
    completed: all.filter((r) => r.status !== "OPEN"),
  };
  const selected = picked?.data ?? null;
  const view = boardView(searchParams.view, !!selected, lists.action.length);
  const rows = view === "selected" ? [] : lists[view];
  const select = (id: string) => `/os/intake?${new URLSearchParams({ view: "selected", request: id, ...(intent ? { intent } : {}) })}`;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_330px] xl:grid-cols-[minmax(0,1fr)_364px] 2xl:grid-cols-[minmax(0,1fr)_420px]" data-testid="intake-board">
      <section className="min-w-0" aria-labelledby="intake-title">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="os-eyebrow">Initiate</div>
            <h1 id="intake-title" className="text-[22px] font-extrabold tracking-[-0.03em]">Requests{intent && <span className="os-muted text-[15px] font-bold"> · {intent === "new" ? "New" : "Enhance"} only</span>}</h1>
          </div>
          <HomeSearch />
        </div>
        <div className="mb-4">
          <BoardTabs base="/os/intake" view={view} selection={selected ? ["request", selected.request_id] : null} extra={intent ? { intent } : undefined}
                     counts={{ action: lists.action.length, active: lists.active.length, completed: lists.completed.length }} />
        </div>
        <SourceError what="Requests" error={list.error} />
        {picked && !selected && <SourceError what="That request" error={picked.error ?? "It does not exist, or belongs to a domain you are not a member of."} />}
        {view === "selected" && selected ? <RequestFocus r={selected} access={access} /> : (
          <BoardList count={rows.length} empty={view === "action"
            ? { title: "Nothing waits on you", body: "When a request reaches a step your role owns, it shows here." }
            : view === "active" ? { title: "No open requests", body: "Start one with New or Enhance." } : { title: "Nothing completed yet" }}>
            {rows.map((r) => <WorkflowRow key={r.request_id} r={r} access={access} href={select(r.request_id)} />)}
          </BoardList>
        )}
      </section>
      <aside>
        <div className="lg:sticky lg:top-[76px]"><StartPanel access={access} inflight={inflight(list.data.requests, intent)} /></div>
      </aside>
    </div>
  );
}
